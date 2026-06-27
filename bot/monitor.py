"""Live wallet monitor — reads the Hyperliquid API directly and auto-refreshes.

An independent, read-only cross-reference for the bot wallet: balance, open
positions (with sizing), and recent fills — the same source of truth Hyperliquid's
own UI uses. Nothing here signs or trades.

Usage:
  ./.venv/bin/python monitor.py                # refresh every 5s
  ./.venv/bin/python monitor.py --interval 2
  ./.venv/bin/python monitor.py --once         # print one snapshot and exit
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime

from hyperliquid.info import Info

from config import API_URL, IS_MAINNET, MAIN_ACCOUNT_ADDRESS

CLEAR = "\033[2J\033[H"  # clear screen + home cursor


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _fills(info: Info, addr: str) -> list[dict]:
    try:
        return info.user_fills(addr) or []
    except Exception:  # noqa: BLE001
        return []


def _account_mode(info: Info, addr: str) -> str:
    try:
        ua = info.post("/info", {"type": "userAbstraction", "user": addr})
        return str(ua) if ua else "standard"
    except Exception:  # noqa: BLE001
        return "unknown"


def snapshot(info: Info, addr: str) -> str:
    perps = info.user_state(addr)
    spot = info.spot_user_state(addr)
    ms = perps.get("marginSummary", {})
    acct_val = _f(ms.get("accountValue"))
    margin_used = _f(ms.get("totalMarginUsed"))
    withdrawable = _f(perps.get("withdrawable"))
    spot_usdc = next((_f(b.get("total")) for b in spot.get("balances", [])
                      if b.get("coin") == "USDC"), 0.0)
    spot_hold = next((_f(b.get("hold")) for b in spot.get("balances", [])
                      if b.get("coin") == "USDC"), 0.0)
    mode = _account_mode(info, addr)
    unified = "unified" in mode.lower()
    upnl = sum(_f(ap.get("position", {}).get("unrealizedPnl"))
               for ap in perps.get("assetPositions", []))
    # In a unified account, spot USDC IS the single collateral pool; a position's
    # margin is reserved FROM it (spot total only drops by fees), so equity is
    # spot USDC + open unrealized PnL. Adding the perps accountValue would
    # double-count the reserved margin.
    collateral = spot_usdc + upnl if unified else acct_val
    # Free collateral the bot can actually deploy. In unified mode the perps
    # `withdrawable` reads 0 (collateral lives in the spot pool); the usable
    # amount is spot total minus what's on hold for open positions. This matches
    # the dashboard's "available margin" and execution.available_margin().
    free_collateral = (spot_usdc - spot_hold) if unified else withdrawable

    net = "MAINNET" if IS_MAINNET else "testnet"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("HYPERLIQUID LIVE MONITOR   (source: Hyperliquid API · read-only)")
    lines.append(f"wallet : {addr}")
    lines.append(f"network: {net}    mode: {mode}    updated: {now}")
    lines.append("=" * 78)

    lines.append("")
    lines.append("ACCOUNT BALANCE")
    lines.append(f"  account equity       : ${collateral:,.4f}"
                 + ("   (spot USDC + uPnL — unified)" if unified else ""))
    lines.append(f"  perps account value  : ${acct_val:,.4f}")
    lines.append(f"  free collateral      : ${free_collateral:,.4f}"
                 + ("   (spot USDC − hold — usable by bot)" if unified else ""))
    lines.append(f"  withdrawable (perps) : ${withdrawable:,.4f}")
    lines.append(f"  margin in use        : ${margin_used:,.4f}")
    lines.append(f"  spot USDC            : ${spot_usdc:,.4f}")

    positions = [ap.get("position", {}) for ap in perps.get("assetPositions", [])]
    positions = [p for p in positions if _f(p.get("szi")) != 0]
    lines.append("")
    lines.append(f"OPEN POSITIONS ({len(positions)})")
    if not positions:
        lines.append("  (none)")
    else:
        lines.append(f"  {'coin':<6}{'side':<6}{'size':>12}{'entry':>12}"
                     f"{'value$':>12}{'uPnL$':>12}{'liq':>12}")
        for p in positions:
            szi = _f(p.get("szi"))
            side = "long" if szi > 0 else "short"
            liq = p.get("liquidationPx")
            liq_s = f"{_f(liq):,.4f}" if liq else "—"
            lines.append(
                f"  {p.get('coin',''):<6}{side:<6}{abs(szi):>12.6f}"
                f"{_f(p.get('entryPx')):>12.4f}{_f(p.get('positionValue')):>12.2f}"
                f"{_f(p.get('unrealizedPnl')):>12.4f}{liq_s:>12}"
            )

    fills = _fills(info, addr)[-10:]
    lines.append("")
    lines.append(f"RECENT FILLS (last {len(fills)})")
    if not fills:
        lines.append("  (none yet)")
    else:
        lines.append(f"  {'time':<10}{'coin':<6}{'dir':<13}{'px':>12}{'size':>12}"
                     f"{'pnl$':>10}{'fee$':>9}")
        for f in fills:
            t = datetime.fromtimestamp(_f(f.get("time")) / 1000).strftime("%H:%M:%S")
            lines.append(
                f"  {t:<10}{f.get('coin',''):<6}{str(f.get('dir','')):<13}"
                f"{_f(f.get('px')):>12.4f}{_f(f.get('sz')):>12.6f}"
                f"{_f(f.get('closedPnl')):>10.4f}{_f(f.get('fee')):>9.4f}"
            )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Live Hyperliquid wallet monitor")
    parser.add_argument("--interval", type=float, default=5.0, help="refresh seconds")
    parser.add_argument("--once", action="store_true", help="print one snapshot and exit")
    args = parser.parse_args()

    if not MAIN_ACCOUNT_ADDRESS:
        print("MAIN_ACCOUNT_ADDRESS must be set in .env")
        return 2

    info = Info(API_URL, skip_ws=True)
    addr = MAIN_ACCOUNT_ADDRESS

    if args.once:
        print(snapshot(info, addr))
        return 0

    try:
        while True:
            out = snapshot(info, addr)
            print(CLEAR + out + f"\n\nrefreshing every {args.interval:g}s — Ctrl-C to quit")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
