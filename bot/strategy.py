"""Strategy Engine.

Ingests real-time L2 order-book data over WebSocket for the assets selected by
the screener and computes Order-Book-Imbalance signals. This module ONLY produces
signals; order routing and risk live in the (separate) Execution & Risk Engine.

Spec: specs/strategy_execution.md (sections 1-2), specs/spec.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from hyperliquid.info import Info

from config import (
    API_URL,
    DISABLE_FLIP_EXIT,
    EXIT_LONG_BELOW,
    EXIT_SHORT_ABOVE,
    LONG_THRESHOLD as _CFG_LONG_THRESHOLD,
    SHORT_THRESHOLD as _CFG_SHORT_THRESHOLD,
    TAKE_PROFIT_PCT as _CFG_TAKE_PROFIT_PCT,
)
from screener import run_screener

# --- Signal logic parameters (from strategy_execution.md section 2) ------------
TOP_LEVELS = 10          # number of bid/ask levels summed for the imbalance
# Entry thresholds are env-overridable via config (obi_v3 widens to 0.75/0.25).
LONG_THRESHOLD = _CFG_LONG_THRESHOLD    # bid ratio >= this -> BUY_LONG
SHORT_THRESHOLD = _CFG_SHORT_THRESHOLD  # bid ratio <= this -> SELL_SHORT

# --- Exit parameters (fix: the original spec defined no profitable exit) -------
# A position is closed when EITHER:
#   1. price reaches TAKE_PROFIT_PCT in favour (a maker limit exit / cap), OR
#   2. (obi_v4) a trailing stop rides behind the best favorable price, OR
#   3. the order-book imbalance reverts past the signal-flip hysteresis bands
#      (unless DISABLE_FLIP_EXIT — obi_v4 lets winners run instead), OR
#   4. the hard stop-loss fires (loss).
# TAKE_PROFIT_PCT is env-overridable (config); default +1.5%.
TAKE_PROFIT_PCT = _CFG_TAKE_PROFIT_PCT

# Signal labels
BUY_LONG = "BUY_LONG"
SELL_SHORT = "SELL_SHORT"
NEUTRAL = "NEUTRAL"

logger = logging.getLogger("strategy")


def should_exit(direction: str, bid_ratio: float) -> bool:
    """Signal-flip exit: True when the imbalance that justified the position has
    reverted past the hysteresis band. A long exits when bid_ratio falls below
    EXIT_LONG_BELOW; a short exits when it rises above EXIT_SHORT_ABOVE. With the
    default 0.50/0.50 this is the original symmetric flip; obi_v3 widens the band
    (e.g. 0.45/0.55) so a position is not cut on noise around neutral.
    Consumed alongside the price-based take-profit / stop-loss.

    When DISABLE_FLIP_EXIT is set (obi_v4) this never fires: positions are
    managed solely by their SL / TP / trailing stop so winners can run.
    """
    if DISABLE_FLIP_EXIT:
        return False
    if direction == BUY_LONG:
        return bid_ratio < EXIT_LONG_BELOW
    if direction == SELL_SHORT:
        return bid_ratio > EXIT_SHORT_ABOVE
    return False


@dataclass(frozen=True)
class SignalResult:
    coin: str
    signal: str
    bid_ratio: float
    bid_volume: float
    ask_volume: float


def _level_volume(levels: list[dict[str, Any]], depth: int) -> float:
    """Sum dollar volume (price * size) over the top `depth` levels."""
    total = 0.0
    for level in levels[:depth]:
        try:
            total += float(level["px"]) * float(level["sz"])
        except (KeyError, TypeError, ValueError):
            continue
    return total


def compute_signal(coin: str, levels: list[list[dict[str, Any]]], depth: int = TOP_LEVELS) -> SignalResult | None:
    """Compute an order-book-imbalance signal from an l2Book `levels` payload.

    `levels` is [bids, asks]; bids are sorted best-first descending, asks ascending.
    Returns None if the book is malformed/empty.
    """
    if not levels or len(levels) < 2:
        return None

    bids, asks = levels[0], levels[1]
    bid_volume = _level_volume(bids, depth)
    ask_volume = _level_volume(asks, depth)
    denom = bid_volume + ask_volume
    if denom <= 0:
        return None

    bid_ratio = bid_volume / denom
    if bid_ratio >= LONG_THRESHOLD:
        signal = BUY_LONG
    elif bid_ratio <= SHORT_THRESHOLD:
        signal = SELL_SHORT
    else:
        signal = NEUTRAL

    return SignalResult(coin, signal, bid_ratio, bid_volume, ask_volume)


class StrategyEngine:
    """Subscribes to l2Book streams and emits signals via a callback.

    The callback receives a SignalResult. Only actionable changes (entering or
    leaving a BUY_LONG/SELL_SHORT state) are forwarded by default to avoid
    spamming the downstream execution engine on every book tick.
    """

    def __init__(
        self,
        info: Info,
        coins: list[str],
        on_signal: Callable[[SignalResult], None],
        emit_on_change_only: bool = True,
    ) -> None:
        self.info = info
        self.coins = coins
        self.on_signal = on_signal
        self.emit_on_change_only = emit_on_change_only
        self._last_signal: dict[str, str] = {}
        self._sub_ids: dict[str, int] = {}

    def _handle_message(self, msg: dict[str, Any]) -> None:
        if msg.get("channel") != "l2Book":
            return
        data = msg.get("data", {})
        coin = data.get("coin")
        levels = data.get("levels")
        if not coin:
            return

        result = compute_signal(coin, levels)
        if result is None:
            return

        if self.emit_on_change_only and self._last_signal.get(coin) == result.signal:
            return
        self._last_signal[coin] = result.signal
        self.on_signal(result)

    def start(self) -> None:
        for coin in self.coins:
            sub = {"type": "l2Book", "coin": coin}
            self._sub_ids[coin] = self.info.subscribe(sub, self._handle_message)
            logger.info("Subscribed to l2Book for %s", coin)

    def stop(self) -> None:
        for coin, sub_id in self._sub_ids.items():
            try:
                self.info.unsubscribe({"type": "l2Book", "coin": coin}, sub_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to unsubscribe %s: %s", coin, exc)
        self._sub_ids.clear()


def _default_signal_handler(result: SignalResult) -> None:
    logger.info(
        "%-10s %-10s bid_ratio=%.3f (bid=$%.0f ask=$%.0f)",
        result.coin,
        result.signal,
        result.bid_ratio,
        result.bid_volume,
        result.ask_volume,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # The screener needs only REST; the strategy engine needs the WebSocket.
    screener_info = Info(API_URL, skip_ws=True)
    coins = run_screener(screener_info)
    if not coins:
        logger.error("Screener returned no assets; nothing to subscribe to.")
        return
    logger.info("Strategy engine targeting screener output: %s", coins)

    info = Info(API_URL, skip_ws=False)
    engine = StrategyEngine(info, coins, _default_signal_handler)
    engine.start()

    try:
        import time

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down strategy engine.")
        engine.stop()
        info.disconnect_websocket()


if __name__ == "__main__":
    main()
