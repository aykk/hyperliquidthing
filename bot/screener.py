"""Asset Screener Module.

Independent background service that queries the Hyperliquid perp universe,
applies hard safety filters (volume / funding / spread), and emits the top-N
highest-volume tickers for the Strategy Engine to subscribe to.

Spec: specs/screener.md
"""

from __future__ import annotations

import logging
import time
from typing import Any

from hyperliquid.info import Info

from config import API_URL

# --- Filter thresholds ---------------------------------------------------------
# NOTE: thresholds revised from the original spec after validating against live
# data (see README "Filter tuning"). Original spec values are noted inline.
MIN_24H_VOLUME_USD = 10_000_000.0  # dayNtlVlm must be strictly greater than this
MAX_ABS_FUNDING = 0.0001           # abs(funding) < this (was 0.0020 -> a no-op:
                                   # the whole universe's max hourly funding is
                                   # ~0.0004, so the old threshold never filtered)
MAX_SPREAD_PCT = 0.08              # spread % must be TIGHTER than this. The original
                                   # spec required spread > 0.05% (wider), which
                                   # excluded every liquid major and forced the bot
                                   # into thin meme books. Flipped to a liquidity gate.
TOP_N = 2                          # number of tickers handed to the strategy engine

POLL_INTERVAL_SECONDS = 600        # run every 10 minutes
RETRY_DELAY_SECONDS = 5            # back off this long after a failed poll

logger = logging.getLogger("screener")


def _to_float(value: Any) -> float | None:
    """Hyperliquid returns numbers as strings; coerce safely."""
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_bid_ask(ctx: dict[str, Any]) -> tuple[float | None, float | None]:
    """Resolve a bid/ask pair from an asset context.

    `meta_and_asset_ctxs` does not return explicit bidPx/askPx fields, so we
    prefer them if present and otherwise fall back to `impactPxs`
    ([impactBid, impactAsk]), which is the closest available proxy.
    """
    bid = _to_float(ctx.get("bidPx"))
    ask = _to_float(ctx.get("askPx"))
    if bid is not None and ask is not None:
        return bid, ask

    impact = ctx.get("impactPxs")
    if isinstance(impact, (list, tuple)) and len(impact) >= 2:
        return _to_float(impact[0]), _to_float(impact[1])

    return None, None


def _spread_pct(bid: float, ask: float) -> float | None:
    """Spread % = ((askPx - bidPx) / askPx) * 100."""
    if ask <= 0:
        return None
    return ((ask - bid) / ask) * 100.0


def screen_assets(meta: dict[str, Any], asset_ctxs: list[dict[str, Any]]) -> list[str]:
    """Pure filtering pipeline. Returns the top-N qualifying tickers.

    Filters are applied sequentially; an asset failing ANY check is dropped.
    Survivors are sorted by highest 24h volume.
    """
    universe = meta.get("universe", [])
    qualifying: list[tuple[str, float]] = []

    for asset, ctx in zip(universe, asset_ctxs):
        name = asset.get("name")
        if not name:
            continue

        # Skip delisted assets if the venue flags them.
        if asset.get("isDelisted"):
            continue

        # A. Volume liquidity filter
        volume = _to_float(ctx.get("dayNtlVlm"))
        if volume is None or volume <= MIN_24H_VOLUME_USD:
            continue

        # B. Funding rate filter
        funding = _to_float(ctx.get("funding"))
        if funding is None or abs(funding) >= MAX_ABS_FUNDING:
            continue

        # C. Spread filter (liquidity gate): keep only tight books so a $10
        #    order and its 1.5% stop / emergency flatten don't suffer heavy
        #    slippage. Drop anything wider than MAX_SPREAD_PCT.
        bid, ask = _extract_bid_ask(ctx)
        if bid is None or ask is None:
            continue
        spread = _spread_pct(bid, ask)
        if spread is None or spread > MAX_SPREAD_PCT:
            continue

        qualifying.append((name, volume))

    qualifying.sort(key=lambda item: item[1], reverse=True)
    return [name for name, _ in qualifying[:TOP_N]]


def run_screener(info: Info) -> list[str]:
    """Fetch the universe + contexts in one call and return the top-N tickers."""
    meta, asset_ctxs = info.meta_and_asset_ctxs()
    return screen_assets(meta, asset_ctxs)


def _log_summary(tickers: list[str]) -> None:
    if tickers:
        logger.info("Top %d selected assets: %s", len(tickers), tickers)
    else:
        logger.warning("No assets passed the screening filters this cycle.")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger.info("Starting screener against %s (every %ds)", API_URL, POLL_INTERVAL_SECONDS)
    info = Info(API_URL, skip_ws=True)

    while True:
        try:
            tickers = run_screener(info)
            _log_summary(tickers)
        except Exception as exc:  # never crash the whole bot on a single bad poll
            logger.error("Screener poll failed: %s. Retrying in %ds.", exc, RETRY_DELAY_SECONDS)
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
