"""Funding-carry prototype (scanner + paper accrual sim).

A different game from the OBI strategies: instead of predicting price, capture
the perpetual *funding* payment with a delta-neutral position. This does NOT
depend on volatility — its edge is structural (the funding rate), which is why
it's worth evaluating when directional scalping shows no edge.

Mechanics (cash-and-carry):
  * Funding > 0  -> longs pay shorts. Collect by SHORT perp + LONG equal-notional
    spot (delta-neutral: price moves cancel, you keep the funding).
  * Funding < 0  -> shorts pay longs. Collect by LONG perp + SHORT spot (the spot
    short needs borrow, usually unavailable on HL spot -> flagged, not traded).

Two modes:
  scan   (default) — rank live funding-carry opportunities by net-of-cost APR,
                     with a liquidity floor and break-even hold time. Read-only.
  paper            — set-and-forget delta-neutral ALLOCATOR. Sizes a capital
                     budget across the best net-APR, liquid, hedgeable,
                     positive-funding coins; accrues funding hourly; and
                     rebalances on a timer (exit funding-decayed carries, rotate
                     into better ones). Writes data/carry_status.json + journals
                     each carry as strategy='carry_v1' so the dashboard shows it.

Allocator policy is env-driven (see CARRY_* knobs below): CARRY_CAPITAL,
CARRY_MAX_POSITIONS, CARRY_MIN_NET_APR, CARRY_HOLD_HORIZON_DAYS, CARRY_MIN_VOL_M,
CARRY_PER_COIN_PCT, CARRY_MAX_DEPLOY_PCT, CARRY_REBALANCE_SEC.

Usage:
  ./.venv/bin/python carry.py                       # scan, default filters
  ./.venv/bin/python carry.py scan --min-vol 50 --top 20
  CARRY_CAPITAL=1000 ./.venv/bin/python carry.py paper   # optional override
  IS_MAINNET=true ./.venv/bin/python carry.py paper      # uses wallet total equity
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hyperliquid.info import Info
from hyperliquid.utils import constants

import config
from config import API_URL, MAIN_ACCOUNT_ADDRESS, IS_MAINNET
from journal import TradeJournal

logger = logging.getLogger("carry")

# --- cost model -------------------------------------------------------------
# Round-trip taker cost to OPEN and CLOSE both legs of a delta-neutral carry.
# perp taker ~0.045%, spot taker ~0.070% (base tier); entry+exit on each leg:
#   2*perp + 2*spot ≈ 0.23%. Env-overridable.
PERP_TAKER = 0.00045
SPOT_TAKER = 0.00070
CARRY_ROUND_TRIP_PCT = float(os.getenv("CARRY_ROUND_TRIP_PCT", 2 * PERP_TAKER + 2 * SPOT_TAKER))

STATUS_PATH = Path(__file__).resolve().parent / "data" / "carry_status.json"
HOURS_PER_YEAR = 24 * 365


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


# --- allocator policy (env-overridable) -------------------------------------
# Sizing budget for the paper sim. By default reads your live wallet total
# equity (perps + spot, same as the dashboard). Override with CARRY_CAPITAL.
CARRY_MAX_POSITIONS = _env_int("CARRY_MAX_POSITIONS", 4)
# Only hold a carry whose net APR (after amortized round-trip cost over the
# hold horizon) clears this floor; below it the carry isn't worth the slot.
CARRY_MIN_NET_APR = _env_float("CARRY_MIN_NET_APR", 3.0)
CARRY_HOLD_HORIZON_DAYS = _env_float("CARRY_HOLD_HORIZON_DAYS", 30.0)
# Liquidity floor (24h notional volume, $M) so we can size/exit without slippage.
CARRY_MIN_VOL_M = _env_float("CARRY_MIN_VOL_M", 20.0)
# Risk caps: max share of capital in any one coin, and max total deployment
# (keeps a cash buffer for fees + margin headroom on the short-perp leg).
CARRY_PER_COIN_PCT = _env_float("CARRY_PER_COIN_PCT", 0.35)
CARRY_MAX_DEPLOY_PCT = _env_float("CARRY_MAX_DEPLOY_PCT", 0.90)
# How often to re-scan and rebalance the book (seconds).
CARRY_REBALANCE_SEC = _env_float("CARRY_REBALANCE_SEC", 3600.0)
# When wallet is on testnet but you want real mainnet funding for the paper sim.
CARRY_FUNDING_MAINNET = os.getenv("CARRY_FUNDING_MAINNET", "true").strip().lower() == "true"
MIN_NOTIONAL_USD = 10.0


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def wallet_total_equity(info: Info, address: str) -> float:
    """Live wallet total equity, matching the dashboard (perps accountValue + spot USDC)."""
    perps = info.user_state(address)
    spot = info.spot_user_state(address)
    acct_val = _f(perps.get("marginSummary", {}).get("accountValue"))
    spot_usdc = next(
        (_f(b.get("total")) for b in spot.get("balances", []) if b.get("coin") == "USDC"),
        0.0,
    )
    return acct_val + spot_usdc


def resolve_carry_capital(info: Info) -> float:
    """Paper sizing budget: explicit CARRY_CAPITAL env, else live wallet total equity."""
    raw = os.getenv("CARRY_CAPITAL")
    if raw is not None and raw.strip() != "":
        return _env_float("CARRY_CAPITAL", config.STARTING_EQUITY)
    if not MAIN_ACCOUNT_ADDRESS:
        logger.warning(
            "[carry] MAIN_ACCOUNT_ADDRESS unset, using STARTING_EQUITY=$%.0f",
            config.STARTING_EQUITY,
        )
        return config.STARTING_EQUITY
    equity = wallet_total_equity(info, MAIN_ACCOUNT_ADDRESS)
    if equity <= 0:
        logger.warning("[carry] wallet equity read as 0, using STARTING_EQUITY=$%.0f",
                       config.STARTING_EQUITY)
        return config.STARTING_EQUITY
    logger.info("[carry] sizing from live wallet total equity: $%.2f (%s)",
                equity, "testnet" if not IS_MAINNET else "mainnet")
    return equity


def funding_api_url() -> str:
    """Market-data network for funding scan/accrual (mainnet by default)."""
    if CARRY_FUNDING_MAINNET and not IS_MAINNET:
        return constants.MAINNET_API_URL
    return API_URL


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Opportunity:
    coin: str
    funding_hourly: float    # signed hourly funding rate (fraction)
    mark: float
    oi_usd: float
    vol_usd: float
    hedgeable: bool          # a matching spot market appears to exist

    @property
    def gross_apr_pct(self) -> float:
        return self.funding_hourly * HOURS_PER_YEAR * 100.0

    @property
    def carry_apr_pct(self) -> float:
        # You position on the collectable side, so the carry yield is |funding|.
        return abs(self.gross_apr_pct)

    @property
    def perp_side(self) -> str:
        # Funding>0: longs pay shorts -> short the perp to collect.
        return "short" if self.funding_hourly > 0 else "long"

    def net_apr_pct(self, hold_days: float) -> float:
        """Carry APR minus the round-trip cost amortized over `hold_days`."""
        amortized_cost_apr = CARRY_ROUND_TRIP_PCT * 100.0 * (365.0 / hold_days)
        return self.carry_apr_pct - amortized_cost_apr

    @property
    def breakeven_days(self) -> float:
        daily = abs(self.funding_hourly) * 24.0
        return (CARRY_ROUND_TRIP_PCT / daily) if daily > 0 else float("inf")


def _spot_token_names(info: Info) -> set[str]:
    """Best-effort set of spot token symbols available on HL (for the hedge leg)."""
    try:
        meta = info.spot_meta()
        names = set()
        for tok in meta.get("tokens", []):
            n = tok.get("name")
            if n:
                names.add(n.upper())
        return names
    except Exception as exc:  # noqa: BLE001
        logger.warning("spot_meta lookup failed: %s", exc)
        return set()


def _is_hedgeable(coin: str, spot_tokens: set[str]) -> bool:
    """A perp `coin` is hedgeable if a matching spot token exists. HL wraps some
    majors as U<SYM> (e.g. UBTC/UETH/USOL), so we check both forms."""
    c = coin.upper()
    return c in spot_tokens or f"U{c}" in spot_tokens


def scan(info: Info, min_vol_usd: float) -> list[Opportunity]:
    meta, ctxs = info.meta_and_asset_ctxs()
    spot_tokens = _spot_token_names(info)
    out: list[Opportunity] = []
    for asset, ctx in zip(meta["universe"], ctxs):
        name = asset.get("name")
        try:
            funding = float(ctx.get("funding"))
            mark = float(ctx.get("markPx"))
            oi = float(ctx.get("openInterest", 0) or 0)
            vol = float(ctx.get("dayNtlVlm", 0) or 0)
        except (TypeError, ValueError):
            continue
        if not name or mark <= 0:
            continue
        if vol < min_vol_usd:
            continue
        out.append(Opportunity(name, funding, mark, oi * mark, vol,
                               _is_hedgeable(name, spot_tokens)))
    # Rank by the clean (positive-funding, hedgeable) net 30d APR first, then by
    # raw carry APR so thin/short-only opportunities still show lower down.
    out.sort(key=lambda o: (o.funding_hourly > 0 and o.hedgeable, o.net_apr_pct(30)), reverse=True)
    return out


def print_scan(opps: list[Opportunity], top: int) -> None:
    print("=" * 100)
    print("HYPERLIQUID FUNDING-CARRY SCANNER   (delta-neutral; source: HL API · read-only)")
    print(f"round-trip cost assumption: {CARRY_ROUND_TRIP_PCT*100:.3f}%   "
          f"updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)
    hdr = (f"{'coin':<8}{'perp side':<10}{'fundingAPR%':>12}{'24hVol$M':>11}"
           f"{'OI$M':>9}{'b/e days':>10}{'net@30d%':>10}{'net@7d%':>10}{'spot hedge':>12}")
    print(hdr)
    print("-" * 100)
    for o in opps[:top]:
        print(f"{o.coin:<8}{o.perp_side+' perp':<10}{o.gross_apr_pct:>12.1f}"
              f"{o.vol_usd/1e6:>11.1f}{o.oi_usd/1e6:>9.1f}{o.breakeven_days:>10.2f}"
              f"{o.net_apr_pct(30):>10.1f}{o.net_apr_pct(7):>10.1f}"
              f"{('yes' if o.hedgeable else 'no/borrow'):>12}")
    print("-" * 100)
    print("notes: fundingAPR sign shows who pays (+ = longs pay shorts -> short the perp to collect).")
    print("       net@Nd = carry APR minus round-trip cost amortized over an N-day hold.")
    print("       'spot hedge=no' means no matching HL spot market (hedge needs borrow) — skip for now.")
    print("       carry is collected only while delta-neutral; an unhedged perp is directional risk.")


# --- paper allocator --------------------------------------------------------
@dataclass
class CarryPosition:
    trade_id: int
    coin: str
    perp_side: str           # 'short' (funding>0) / 'long' (funding<0)
    notional: float          # USD per leg (spot long == perp short)
    entry_mark: float
    entry_funding: float
    accrued: float = 0.0     # funding collected so far, net of entry cost (USD)


@dataclass
class CarryAllocator:
    """Set-and-forget delta-neutral funding-carry allocator (paper).

    Holds up to N delta-neutral carries on the best net-APR, liquid, hedgeable,
    positive-funding coins; accrues funding hourly; and rebalances on a timer —
    exiting carries whose funding flipped or decayed below the floor and rotating
    capital into better ones. `equity` tracks realized PnL (funding − fees);
    `capital` is the fixed sizing budget.
    """

    info: Info
    journal: TradeJournal
    capital: float
    equity: float
    positions: dict[str, CarryPosition] = field(default_factory=dict)
    started_at: str = field(default_factory=_utc_iso)
    _last_funding_hour: int | None = None

    # --- sizing / selection ---
    @property
    def deployed(self) -> float:
        return sum(p.notional for p in self.positions.values())

    def _size_for_new(self) -> float:
        budget = self.capital * CARRY_MAX_DEPLOY_PCT
        free = budget - self.deployed
        slot = budget / max(CARRY_MAX_POSITIONS, 1)
        per_coin_cap = self.capital * CARRY_PER_COIN_PCT
        return max(0.0, min(slot, per_coin_cap, free))

    def _targets(self, opps: list[Opportunity]) -> list[Opportunity]:
        """The carries we WANT to hold: hedgeable, positive funding, liquid, and
        clearing the net-APR floor — ranked best-first, capped at MAX_POSITIONS."""
        elig = [
            o for o in opps
            if o.hedgeable and o.funding_hourly > 0
            and o.vol_usd >= CARRY_MIN_VOL_M * 1e6
            and o.net_apr_pct(CARRY_HOLD_HORIZON_DAYS) >= CARRY_MIN_NET_APR
        ]
        elig.sort(key=lambda o: o.net_apr_pct(CARRY_HOLD_HORIZON_DAYS), reverse=True)
        return elig[:CARRY_MAX_POSITIONS]

    # --- lifecycle ---
    def enter(self, opp: Opportunity, notional: float) -> None:
        cost = notional * CARRY_ROUND_TRIP_PCT  # open+close fees charged up front
        self.equity -= cost
        size = notional / opp.mark
        trade_id = self.journal.record_entry(
            mode="paper", strategy="carry_v1", coin=opp.coin, side=opp.perp_side,
            signal="CARRY", entry_px=opp.mark, size=size, funding=opp.funding_hourly,
        )
        self.positions[opp.coin] = CarryPosition(
            trade_id, opp.coin, opp.perp_side, notional, opp.mark,
            entry_funding=opp.funding_hourly, accrued=-cost,
        )
        logger.info("[carry] ENTER %s %s perp $%.2f (fundingAPR=%.1f%% net@%dd=%.1f%%) cost=$%.4f",
                    opp.coin, opp.perp_side, notional, opp.gross_apr_pct,
                    int(CARRY_HOLD_HORIZON_DAYS), opp.net_apr_pct(CARRY_HOLD_HORIZON_DAYS), cost)

    def close(self, coin: str, reason: str) -> None:
        pos = self.positions.pop(coin, None)
        if pos is None:
            return
        fees = pos.notional * CARRY_ROUND_TRIP_PCT
        self.journal.record_exit(
            pos.trade_id, exit_px=pos.entry_mark, exit_reason=reason,
            fees=fees, realized_pnl=pos.accrued, equity_after=self.equity,
        )
        logger.info("[carry] CLOSE %s (%s) accrued=$%.4f", coin, reason, pos.accrued)

    def rebalance(self, opps: list[Opportunity]) -> None:
        targets = {o.coin: o for o in self._targets(opps)}
        # 1) Exit held carries that are no longer wanted (funding flipped to the
        #    wrong side, decayed below floor, or fell out of the top set).
        for coin in list(self.positions):
            if coin not in targets:
                self.close(coin, "funding_decay")
        # 2) Rotate freed capital into the best carries we don't yet hold.
        for coin, opp in targets.items():
            if coin in self.positions or len(self.positions) >= CARRY_MAX_POSITIONS:
                continue
            notional = self._size_for_new()
            if notional < MIN_NOTIONAL_USD:
                break
            self.enter(opp, notional)

    def accrue(self, funding_map: dict[str, float], now: float | None = None) -> None:
        """Credit funding once per UTC hour for each held carry."""
        now = now if now is not None else time.time()
        hour = int(datetime.fromtimestamp(now, tz=timezone.utc).replace(
            minute=0, second=0, microsecond=0).timestamp())
        if self._last_funding_hour is None:
            self._last_funding_hour = hour
            return
        if hour <= self._last_funding_hour:
            return
        self._last_funding_hour = hour
        for pos in self.positions.values():
            rate = funding_map.get(pos.coin)
            if rate is None:
                continue
            # Short perp collects when funding>0; long perp collects when funding<0.
            collecting = (pos.perp_side == "short" and rate > 0) or (
                pos.perp_side == "long" and rate < 0)
            income = (pos.notional * abs(rate)) if collecting else -(pos.notional * abs(rate))
            pos.accrued += income
            self.equity += income
            logger.info("[carry] funding %s rate=%.6f -> %+.4f (equity=$%.4f)",
                        pos.coin, rate, income, self.equity)

    def close_all(self, reason: str = "shutdown") -> None:
        for coin in list(self.positions):
            self.close(coin, reason)

    def write_status(self, state: str) -> None:
        try:
            STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
            deployed = self.deployed
            est_apr = sum(p.entry_funding * HOURS_PER_YEAR * 100.0 * p.notional
                          for p in self.positions.values())
            est_apr = est_apr / deployed if deployed else 0.0
            # Net APR after amortized round-trip over the hold horizon (portfolio avg).
            net_apr = est_apr - (CARRY_ROUND_TRIP_PCT * 100.0 * 365.0 / CARRY_HOLD_HORIZON_DAYS) if deployed else 0.0
            entry_cost = sum(p.notional * CARRY_ROUND_TRIP_PCT for p in self.positions.values())
            funding_earned = sum(p.accrued + p.notional * CARRY_ROUND_TRIP_PCT
                                 for p in self.positions.values())
            breakeven_pct = min(100.0, (funding_earned / entry_cost * 100.0) if entry_cost else 0.0)
            positions_detail = [
                {
                    "coin": p.coin,
                    "side": p.perp_side,
                    "notional": round(p.notional, 2),
                    "accrued": round(p.accrued, 4),
                    "funding_apr_pct": round(abs(p.entry_funding) * HOURS_PER_YEAR * 100.0, 2),
                    "breakeven_days": round(
                        CARRY_ROUND_TRIP_PCT / (abs(p.entry_funding) * 24.0), 2
                    ) if p.entry_funding else None,
                }
                for p in self.positions.values()
            ]
            STATUS_PATH.write_text(json.dumps({
                "pid": os.getpid(),
                "state": state,
                "mode": "paper",
                "strategy": "carry_v1",
                "network": "mainnet" if funding_api_url() == constants.MAINNET_API_URL else "testnet",
                "wallet_network": "testnet" if not IS_MAINNET else "mainnet",
                "paper_mode": True,
                "total_equity": round(self.capital, 2),
                "capital": round(self.capital, 2),  # legacy key
                "equity": round(self.equity, 4),
                "pnl": round(self.equity - self.capital, 4),
                "deployed": round(deployed, 2),
                "deploy_pct": round(deployed / self.capital * 100.0, 1) if self.capital else 0.0,
                "blended_apr_pct": round(est_apr, 2),
                "net_apr_30d_pct": round(net_apr, 2),
                "entry_cost_paid": round(entry_cost, 4),
                "funding_earned": round(funding_earned, 4),
                "breakeven_pct": round(breakeven_pct, 1),
                "rebalance_minutes": round(CARRY_REBALANCE_SEC / 60.0, 0),
                "open_positions": len(self.positions),
                "coins": list(self.positions.keys()),
                "positions": positions_detail,
                "started_at": self.started_at,
                "updated_at": _utc_iso(),
            }, indent=2))
        except Exception as exc:  # noqa: BLE001
            logger.warning("carry status write failed: %s", exc)


_STOP = threading.Event()


def _request_stop(signum, _frame) -> None:  # noqa: ANN001
    logger.info("[carry] signal %s -> shutting down", signum)
    _STOP.set()


def run_paper(info: Info, min_vol_usd: float) -> None:
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
    journal = TradeJournal()
    budget = resolve_carry_capital(info)
    funding_info = Info(funding_api_url(), skip_ws=True)
    engine = CarryAllocator(funding_info, journal, capital=budget, equity=budget)
    funding_net = "mainnet" if funding_api_url() == constants.MAINNET_API_URL else "testnet"
    wallet_net = "testnet" if not IS_MAINNET else "mainnet"
    logger.info(
        "[carry] allocator: wallet total_equity=$%.2f (%s) · funding=%s · paper (no real orders) "
        "maxPos=%d minNetAPR=%.1f%% horizon=%dd perCoin<=%.0f%% deploy<=%.0f%% rebalance=%.0fm",
        budget, wallet_net, funding_net,
        CARRY_MAX_POSITIONS, CARRY_MIN_NET_APR, int(CARRY_HOLD_HORIZON_DAYS),
        CARRY_PER_COIN_PCT * 100, CARRY_MAX_DEPLOY_PCT * 100, CARRY_REBALANCE_SEC / 60,
    )

    opps = scan(funding_info, 0)
    engine.rebalance(opps)
    if not engine.positions:
        logger.warning("[carry] no carry clears the net-APR floor right now; waiting for rebalance")
    engine.write_status("running")

    funding_map = {o.coin: o.funding_hourly for o in opps}
    last_refresh = time.time()
    last_rebalance = time.time()
    last_status = 0.0
    try:
        while not _STOP.is_set():
            time.sleep(1)
            now = time.time()
            if now - last_refresh > 300:  # refresh funding every 5 min
                opps = scan(funding_info, 0)
                funding_map = {o.coin: o.funding_hourly for o in opps}
                last_refresh = now
            if now - last_rebalance > CARRY_REBALANCE_SEC:
                engine.rebalance(opps)
                last_rebalance = now
            engine.accrue(funding_map, now)
            if now - last_status >= 5:
                engine.write_status("running")
                last_status = now
    finally:
        engine.close_all("shutdown")
        engine.write_status("stopped")
        logger.info("[carry] final equity=$%.4f (pnl=$%.4f)", engine.equity,
                    engine.equity - engine.capital)
        journal.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    p = argparse.ArgumentParser(description="Hyperliquid funding-carry scanner / paper sim")
    p.add_argument("mode", nargs="?", default="scan", choices=["scan", "paper"])
    p.add_argument("--min-vol", type=float, default=20.0,
                   help="scan liquidity floor, 24h $M (default 20). paper uses CARRY_MIN_VOL_M.")
    p.add_argument("--top", type=int, default=15, help="rows to show in scan")
    args = p.parse_args()

    info = Info(API_URL, skip_ws=True)

    if args.mode == "scan":
        print_scan(scan(info, args.min_vol * 1e6), args.top)
        return 0
    run_paper(info, args.min_vol * 1e6)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
