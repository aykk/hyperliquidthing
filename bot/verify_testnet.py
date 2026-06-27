"""Testnet validation harness.

Verifies the two things the spec/unified-mode reconciliation left open, on TESTNET
only (never mainnet — see the hard guard below):

  1. Per-position ISOLATED margin actually applies inside a unified account.
  2. Positions report correctly via user_state (szi, leverage.type, marginUsed).

Safe by default: with no flags it only READS state (connectivity, agent auth,
account mode, free margin) and sends nothing. Pass --place to run a real round
trip on testnet: set the coin to 1x isolated, open a tiny position, confirm the
position is reported as isolated, then market-close it and confirm we are flat.

Prereqs for --place (see bot/README.md "Testnet validation"):
  * .env has IS_MAINNET=false
  * .env WALLET_PRIVATE_KEY / AGENT_ADDRESS are a TESTNET agent approved for the
    testnet master account
  * the testnet master account holds mock USDC from the testnet faucet

Run:
  ./.venv/bin/python verify_testnet.py            # read-only checks
  ./.venv/bin/python verify_testnet.py --place     # round-trip (testnet only)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import eth_account

import config
from execution import LEVERAGE, build_engine

logger = logging.getLogger("verify_testnet")

DEFAULT_COIN = "ETH"
DEFAULT_NOTIONAL_USD = 12.0  # a little above the $10 protocol minimum


def _fmt(v: object) -> str:
    return "—" if v is None else str(v)


def _find_position(state: dict, coin: str) -> dict | None:
    for ap in state.get("assetPositions", []):
        pos = ap.get("position", {})
        if pos.get("coin") == coin and float(pos.get("szi", 0) or 0) != 0:
            return pos
    return None


def read_only_checks(engine) -> None:
    logger.info("network: %s (%s)", "TESTNET" if not config.IS_MAINNET else "MAINNET", config.API_URL)

    # Agent auth: the configured private key must derive AGENT_ADDRESS and be an
    # approved agent of the master account.
    agent = eth_account.Account.from_key(config.WALLET_PRIVATE_KEY)
    derived = agent.address
    logger.info("signer key derives: %s", derived)
    direct = derived.lower() == (engine.master_address or "").lower()
    if direct:
        # The signer IS the funded account — trading directly, no agent needed.
        logger.info("signer == master account -> direct trading (no agent approval needed)")
    else:
        if config.AGENT_ADDRESS and derived.lower() != config.AGENT_ADDRESS.lower():
            logger.warning("  derived signer != AGENT_ADDRESS (%s) — check .env", config.AGENT_ADDRESS)
        try:
            agents = engine.info.post("/info", {"type": "extraAgents", "user": engine.master_address})
            approved = {a.get("address", "").lower() for a in (agents or [])}
            ok = derived.lower() in approved
            logger.info("agent approved on master: %s%s", ok, "" if ok else f" (approved={approved})")
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not query extraAgents: %s", exc)

    logger.info("account mode: %s (unified=%s)", engine.account_mode(), engine._is_unified)
    state = engine.fetch_state()
    logger.info("free margin: $%.2f", engine.available_margin(state))
    logger.info("open positions: %s", engine.open_positions(state))


def round_trip(engine, coin: str, notional_usd: float) -> bool:
    quotes = engine.best_bid_ask(coin)
    if quotes is None:
        logger.error("no book for %s; cannot size order", coin)
        return False
    best_bid, best_ask = quotes
    mid = (best_bid + best_ask) / 2
    sz = engine.size_for_notional(coin, notional_usd, mid)
    logger.info("round-trip %s: mid=%.6g size=%s (~$%.2f)", coin, mid, sz, sz * mid)

    # 1) Force the coin to 1x ISOLATED before opening.
    engine._ensure_isolated(coin)

    # 2) Open a tiny LONG with a marketable order so it fills on a thin testnet book.
    logger.info("opening %s long (market)…", coin)
    res = engine.exchange.market_open(coin, True, sz)
    logger.info("market_open -> %s", res)

    # 3) Poll user_state until the position appears.
    pos = None
    for _ in range(15):
        time.sleep(1)
        pos = _find_position(engine.fetch_state(), coin)
        if pos:
            break
    if not pos:
        logger.error("position never appeared in user_state after open")
        return False

    lev = pos.get("leverage", {}) or {}
    lev_type = lev.get("type")
    logger.info(
        "POSITION REPORT: coin=%s szi=%s entryPx=%s leverage=%sx %s marginUsed=%s liqPx=%s",
        coin, _fmt(pos.get("szi")), _fmt(pos.get("entryPx")),
        _fmt(lev.get("value")), _fmt(lev_type), _fmt(pos.get("marginUsed")),
        _fmt(pos.get("liquidationPx")),
    )
    isolated_ok = lev_type == "isolated"
    lev_ok = str(lev.get("value")) == str(LEVERAGE)
    logger.info("isolated margin applied: %s", isolated_ok)
    logger.info("leverage == %sx: %s", LEVERAGE, lev_ok)

    # 4) Flatten.
    logger.info("closing %s…", coin)
    logger.info("market_close -> %s", engine.exchange.market_close(coin))
    flat = False
    for _ in range(15):
        time.sleep(1)
        if _find_position(engine.fetch_state(), coin) is None:
            flat = True
            break
    logger.info("flat after close: %s", flat)

    ok = isolated_ok and lev_ok and flat
    logger.info("ROUND-TRIP RESULT: %s", "PASS" if ok else "FAIL")
    return ok


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Hyperliquid testnet validation harness")
    parser.add_argument("--place", action="store_true",
                        help="place a real round-trip order on TESTNET (default: read-only)")
    parser.add_argument("--coin", default=DEFAULT_COIN, help=f"coin to test (default {DEFAULT_COIN})")
    parser.add_argument("--notional", type=float, default=DEFAULT_NOTIONAL_USD,
                        help=f"order notional in USD (default {DEFAULT_NOTIONAL_USD})")
    args = parser.parse_args()

    # HARD GUARD: --place must never touch mainnet funds.
    if args.place and config.IS_MAINNET:
        logger.error("REFUSING to --place on MAINNET. Set IS_MAINNET=false in .env first.")
        return 2

    engine = build_engine(dry_run=not args.place)
    read_only_checks(engine)

    if not args.place:
        logger.info("read-only checks complete. re-run with --place to validate isolated margin on testnet.")
        return 0

    ok = round_trip(engine, args.coin, args.notional)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
