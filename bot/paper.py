"""Paper-trading engine.

Runs the REAL strategy logic against LIVE mainnet order-book + trade data and
simulates fills + P&L without sending any orders. This is the iteration loop for
measuring whether the signal actually has an edge (testnet books are too thin to
be meaningful for this). Every simulated trade is written to the SQLite
TradeJournal.

Fill model (queue-aware maker entries, depth-aware taker exits):
  * Entry: a post-only limit at the passive side (best bid for long / best ask
    for short). We measure `queue_ahead` = the resting size at our price level
    at placement time (FIFO queue we sit behind). We then watch the LIVE trades
    stream: every aggressor trade that executes at our level on our side
    (long: aggressive SELLS at px <= our px; short: aggressive BUYS at px >= our
    px) burns down `queue_ahead`. Once it reaches <= 0 we are filled at our px.
    As a fallback we also fill if the book trades clean through our level
    (long: best_ask <= px; short: best_bid >= px). Unfilled after the 15s TIF we
    cancel. This models real maker fill probability and adverse selection.
  * Exit: take-profit is a maker limit at the TP price (no slippage). Stop-loss
    and signal-flip are taker market exits: we walk the LIVE L2 book on the
    opposite side for our full size to get a realistic size-weighted average
    fill price (a long sells into the bids; a short buys from the asks), so
    slippage against the trigger/touch price is captured.

Trades-stream side convention (verified empirically against the live BTC book,
2026-06: aggressive BUYS that lift the ask carry side "B"; aggressive SELLS that
hit the bid carry side "A"). See `_aggressor_is_buy`.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hyperliquid.info import Info

import config
from config import API_URL, STARTING_EQUITY, STRATEGY_NAME
from execution import MAX_CONCURRENT_POSITIONS, ORDER_NOTIONAL_USD, ORDER_TIF_SECONDS, STOP_LOSS_PCT
from journal import TradeJournal
from screener import run_screener
from strategy import (
    BUY_LONG,
    SELL_SHORT,
    TAKE_PROFIT_PCT,
    compute_signal,
    should_exit,
)

logger = logging.getLogger("paper")

MAKER_FEE = 0.00015   # 0.015% base-tier maker
TAKER_FEE = 0.00045   # 0.045% base-tier taker

# If no WS message arrives for this long, treat the socket as dead and rebuild
# it. l2Book for liquid coins ticks every few seconds, so 60s of silence is a
# reliable "the feed is gone" signal (e.g. the host slept or the network blipped).
WS_STALE_SECONDS = 60.0

# Heartbeat/status file the dashboard reads to show run state + start/stop the
# engine. Lives alongside the journal DB (gitignored).
STATUS_PATH = Path(__file__).resolve().parent / "data" / "bot_status.json"

# Hyperliquid trades-stream aggressor side labels (verified empirically).
TRADE_SIDE_AGGRESSIVE_BUY = "B"   # lifts the ask
TRADE_SIDE_AGGRESSIVE_SELL = "A"  # hits the bid


def _aggressor_is_buy(side: str) -> bool:
    """True if a trade's `side` denotes an aggressive BUY (taker lifted the ask).

    Verified empirically against the live book: "B" trades print at/above the
    ask (aggressive buys), "A" trades print at/below the bid (aggressive sells).
    """
    return side == TRADE_SIDE_AGGRESSIVE_BUY


@dataclass
class PendingOrder:
    coin: str
    side: str          # BUY_LONG / SELL_SHORT
    px: float
    size: float
    placed: float      # epoch seconds
    bid_ratio: float
    spread_pct: float
    queue_ahead: float  # resting size ahead of us in the FIFO queue at our level


@dataclass
class PaperPosition:
    trade_id: int
    coin: str
    is_long: bool
    entry_px: float
    size: float
    sl_px: float
    tp_px: float
    opened_at: float = 0.0       # epoch secs; guards MIN_HOLD_SECONDS on flip exit
    entry_fee_rate: float = MAKER_FEE  # maker for resting fills, taker for crosses
    peak_px: float = 0.0         # best favorable price seen (obi_v4 trailing stop)


@dataclass
class PaperEngine:
    info: Info
    coins: list[str]
    journal: TradeJournal
    equity: float = field(default_factory=lambda: STARTING_EQUITY)
    strategy: str = field(default_factory=lambda: STRATEGY_NAME)
    funding_map: dict[str, float] = field(default_factory=dict)
    _pending: dict[str, PendingOrder] = field(default_factory=dict)
    _positions: dict[str, PaperPosition] = field(default_factory=dict)
    _books: dict[str, list[list[dict[str, Any]]]] = field(default_factory=dict)
    # aggressor-flow buffer per coin: deque of (ts, is_buy, notional)
    _flow: dict[str, deque[tuple[float, bool, float]]] = field(default_factory=dict)
    _sub_ids: dict[str, int] = field(default_factory=dict)
    _trade_sub_ids: dict[str, int] = field(default_factory=dict)
    # Reentrant lock: l2Book and trades callbacks share the WS thread, but we
    # guard all mutable state explicitly so the model stays correct even if the
    # SDK ever dispatches callbacks from separate threads.
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _last_funding_hour: int | None = None
    # Timestamp of the last WS message seen (any coin). The main loop watches
    # this to detect a dead/stale socket (e.g. after the host sleeps) and
    # rebuild the connection, since the SDK does not auto-reconnect.
    _last_msg: float = field(default_factory=time.time)

    # --- helpers ---------------------------------------------------------------
    def _available(self) -> float:
        return self.equity - sum(p.size * p.entry_px for p in self._positions.values())

    @staticmethod
    def _best_bid_ask(levels: list[list[dict[str, Any]]]) -> tuple[float, float] | None:
        if not levels or len(levels) < 2 or not levels[0] or not levels[1]:
            return None
        return float(levels[0][0]["px"]), float(levels[1][0]["px"])

    @staticmethod
    def _walk_book(side_levels: list[dict[str, Any]], size: float) -> float | None:
        """Size-weighted average fill price for sweeping `size` through one side
        of the book (levels sorted best-first). Models taker slippage. If the
        visible depth is insufficient, the remainder is priced at the worst
        (deepest) visible level.
        """
        if not side_levels or size <= 0:
            return None
        remaining = size
        cost = 0.0
        last_px = None
        for lvl in side_levels:
            try:
                px = float(lvl["px"])
                sz = float(lvl["sz"])
            except (KeyError, TypeError, ValueError):
                continue
            last_px = px
            take = min(remaining, sz)
            cost += take * px
            remaining -= take
            if remaining <= 0:
                break
        if last_px is None:
            return None
        if remaining > 0:  # not enough visible depth; fill rest at worst level
            cost += remaining * last_px
        return cost / size

    # --- l2Book tick handler ---------------------------------------------------
    def _handle_book(self, msg: dict[str, Any]) -> None:
        if msg.get("channel") != "l2Book":
            return
        data = msg.get("data", {})
        coin = data.get("coin")
        levels = data.get("levels")
        quotes = self._best_bid_ask(levels) if levels else None
        if not coin or quotes is None:
            return
        best_bid, best_ask = quotes
        self._last_msg = time.time()

        with self._lock:
            self._books[coin] = levels
            self._manage_position(coin, best_bid, best_ask, levels)
            self._manage_pending(coin, best_bid, best_ask)
            self._maybe_enter(coin, best_bid, best_ask, levels)

    # --- trades stream handler -------------------------------------------------
    def _handle_trades(self, msg: dict[str, Any]) -> None:
        if msg.get("channel") != "trades":
            return
        trades = msg.get("data", [])
        if not trades:
            return
        now = time.time()
        self._last_msg = now
        cutoff = now - config.FLOW_WINDOW_SECONDS
        with self._lock:
            for t in trades:
                coin = t.get("coin")
                if not coin:
                    continue
                try:
                    px = float(t["px"])
                    sz = float(t["sz"])
                except (KeyError, TypeError, ValueError):
                    continue
                # record realized aggressor flow (for the entry confirmation gate)
                buf = self._flow.get(coin)
                if buf is not None:
                    buf.append((now, _aggressor_is_buy(t.get("side", "")), px * sz))
                # burn down the FIFO queue of any resting maker entry on this coin
                order = self._pending.get(coin)
                if order is not None:
                    self._apply_trade_to_queue(order, t.get("side", ""), px, sz)
            for buf in self._flow.values():  # prune old samples
                while buf and buf[0][0] < cutoff:
                    buf.popleft()

    def _recent_flow(self, coin: str) -> tuple[float, float]:
        """(buy_notional, sell_notional) of aggressor flow within the window."""
        buf = self._flow.get(coin)
        if not buf:
            return (0.0, 0.0)
        cutoff = time.time() - config.FLOW_WINDOW_SECONDS
        while buf and buf[0][0] < cutoff:
            buf.popleft()
        buy_n = sum(n for _ts, is_buy, n in buf if is_buy)
        sell_n = sum(n for _ts, is_buy, n in buf if not is_buy)
        return (buy_n, sell_n)

    def _entry_allowed(self, coin: str, signal: str, spread_pct: float) -> bool:
        """obi_v3 pre-entry gates (mirror of the live runner)."""
        if spread_pct is not None and spread_pct > config.MAX_ENTRY_SPREAD_PCT:
            return False
        if config.REQUIRE_FLOW_CONFIRM or config.MIN_FLOW_NOTIONAL_USD > 0:
            buy_n, sell_n = self._recent_flow(coin)
            total = buy_n + sell_n
            if total < config.MIN_FLOW_NOTIONAL_USD:
                return False
            if config.REQUIRE_FLOW_CONFIRM and total > 0:
                want_buy = signal == BUY_LONG
                agree = (buy_n if want_buy else sell_n) / total
                if agree < config.FLOW_CONFIRM_RATIO:
                    return False
        return True

    def _apply_trade_to_queue(self, order: PendingOrder, side: str, px: float, sz: float) -> None:
        # TIF expiry guard (a quiet book may not tick l2Book between trades).
        if time.time() - order.placed >= ORDER_TIF_SECONDS:
            logger.info("[paper] %s entry unfilled in %ds -> cancel", order.coin, ORDER_TIF_SECONDS)
            self._pending.pop(order.coin, None)
            return

        is_buy = _aggressor_is_buy(side)
        # A resting BUY (long) at P is consumed by aggressive SELLS at px <= P.
        # A resting SELL (short) at P is consumed by aggressive BUYS at px >= P.
        if order.side == BUY_LONG:
            hits_us = (not is_buy) and px <= order.px
        else:
            hits_us = is_buy and px >= order.px
        if not hits_us:
            return

        order.queue_ahead -= sz
        if order.queue_ahead <= 0:
            logger.info("[paper] %s queue exhausted by trades -> maker fill @ %.6g",
                        order.coin, order.px)
            self._open_from(order)
            self._pending.pop(order.coin, None)

    def _manage_position(self, coin, best_bid, best_ask, levels) -> None:
        pos = self._positions.get(coin)
        if not pos:
            return
        sig = compute_signal(coin, levels)
        ratio = sig.bid_ratio if sig else 0.5
        # MIN_HOLD_SECONDS guards only the signal-flip exit, never the bracket.
        can_flip = (time.time() - pos.opened_at) >= config.MIN_HOLD_SECONDS

        if pos.is_long:
            pos.peak_px = max(pos.peak_px, best_bid)  # track the high-water mark
            if best_bid <= pos.sl_px:
                self._close(pos, self._taker_exit_px(pos, levels, best_bid), "stop_loss", TAKER_FEE)
            elif best_bid >= pos.tp_px:
                self._close(pos, pos.tp_px, "take_profit", MAKER_FEE)
            elif self._trailing_hit(pos, best_bid):
                self._close(pos, self._taker_exit_px(pos, levels, best_bid), "trailing_stop", TAKER_FEE)
            elif can_flip and should_exit(BUY_LONG, ratio):
                self._close(pos, self._taker_exit_px(pos, levels, best_bid), "signal_flip", TAKER_FEE)
        else:
            pos.peak_px = min(pos.peak_px, best_ask)  # track the low-water mark
            if best_ask >= pos.sl_px:
                self._close(pos, self._taker_exit_px(pos, levels, best_ask), "stop_loss", TAKER_FEE)
            elif best_ask <= pos.tp_px:
                self._close(pos, pos.tp_px, "take_profit", MAKER_FEE)
            elif self._trailing_hit(pos, best_ask):
                self._close(pos, self._taker_exit_px(pos, levels, best_ask), "trailing_stop", TAKER_FEE)
            elif can_flip and should_exit(SELL_SHORT, ratio):
                self._close(pos, self._taker_exit_px(pos, levels, best_ask), "signal_flip", TAKER_FEE)

    @staticmethod
    def _trailing_hit(pos: PaperPosition, ref_px: float) -> bool:
        """obi_v4 trailing stop. Once price has run TRAIL_ACTIVATE_PCT past entry,
        a stop rides TRAIL_PCT behind the best favorable price (pos.peak_px).
        Returns True when `ref_px` (best_bid for a long, best_ask for a short)
        has retraced through that stop. Off when TRAIL_PCT <= 0."""
        if config.TRAIL_PCT <= 0:
            return False
        if pos.is_long:
            armed = (pos.peak_px - pos.entry_px) / pos.entry_px >= config.TRAIL_ACTIVATE_PCT
            return armed and ref_px <= pos.peak_px * (1 - config.TRAIL_PCT)
        armed = (pos.entry_px - pos.peak_px) / pos.entry_px >= config.TRAIL_ACTIVATE_PCT
        return armed and ref_px >= pos.peak_px * (1 + config.TRAIL_PCT)

    def _taker_exit_px(self, pos: PaperPosition, levels, fallback_px: float) -> float:
        """Realistic taker exit price by walking the opposite book side for our
        full size. A long exit sells into the bids (levels[0]); a short exit buys
        from the asks (levels[1]). Falls back to the touch price if depth is
        unreadable."""
        side_levels = levels[0] if pos.is_long else levels[1]
        walked = self._walk_book(side_levels, pos.size)
        return walked if walked is not None else fallback_px

    def _manage_pending(self, coin, best_bid, best_ask) -> None:
        order = self._pending.get(coin)
        if not order:
            return
        age = time.time() - order.placed
        # hybrid: if the post-only hasn't filled within the chase window, cross
        # the spread as a taker (walk the book for size) and pay the taker fee.
        if config.ENTRY_MODE == "hybrid" and age >= config.HYBRID_CHASE_SECONDS:
            is_buy = order.side == BUY_LONG
            levels = self._books.get(coin)
            ref_px = best_ask if is_buy else best_bid
            side_levels = (levels[1] if is_buy else levels[0]) if levels else None
            fill_px = (self._walk_book(side_levels, order.size) if side_levels else None) or ref_px
            order.px = fill_px
            logger.info("[paper] %s hybrid chase -> taker-cross fill @ %.6g", coin, fill_px)
            self._open_from(order, entry_fee_rate=TAKER_FEE)
            del self._pending[coin]
            return
        # TIF expiry (maker mode)
        if age >= ORDER_TIF_SECONDS:
            logger.info("[paper] %s entry unfilled in %ds -> cancel", coin, ORDER_TIF_SECONDS)
            del self._pending[coin]
            return
        # Fallback fill: the book trades clean through our resting maker level.
        filled = (order.side == BUY_LONG and best_ask <= order.px) or (
            order.side == SELL_SHORT and best_bid >= order.px
        )
        if filled:
            logger.info("[paper] %s book traded through level -> maker fill @ %.6g", coin, order.px)
            self._open_from(order)
            del self._pending[coin]

    def _maybe_enter(self, coin, best_bid, best_ask, levels) -> None:
        if coin in self._positions or coin in self._pending:
            return
        if len(self._positions) >= MAX_CONCURRENT_POSITIONS:
            return
        if self._available() < ORDER_NOTIONAL_USD:
            return
        sig = compute_signal(coin, levels)
        if sig is None or sig.signal not in (BUY_LONG, SELL_SHORT):
            return

        is_buy = sig.signal == BUY_LONG
        spread_pct = (best_ask - best_bid) / best_ask * 100 if best_ask else 0.0
        # obi_v3 entry gates: wide spread / unconfirmed-or-thin aggressor flow.
        if not self._entry_allowed(coin, sig.signal, spread_pct):
            return

        # taker mode: cross the spread immediately (no resting maker order).
        if config.ENTRY_MODE == "taker":
            ref_px = best_ask if is_buy else best_bid
            size = ORDER_NOTIONAL_USD / ref_px
            side_levels = levels[1] if is_buy else levels[0]
            fill_px = self._walk_book(side_levels, size) or ref_px
            order = PendingOrder(coin, sig.signal, fill_px, size, time.time(),
                                 sig.bid_ratio, spread_pct, 0.0)
            self._open_from(order, entry_fee_rate=TAKER_FEE)
            logger.info("[paper] %s %s taker-cross fill @ %.6g (bid_ratio=%.2f)",
                        coin, sig.signal, fill_px, sig.bid_ratio)
            return

        # maker / hybrid: rest a post-only at the passive side.
        px = best_bid if is_buy else best_ask
        size = ORDER_NOTIONAL_USD / px
        # Queue ahead = resting size already sitting at our price level (the top
        # level on our side, since we join at best bid/ask).
        queue_ahead = self._resting_size_at_top(levels, is_buy)
        self._pending[coin] = PendingOrder(
            coin, sig.signal, px, size, time.time(), sig.bid_ratio, spread_pct, queue_ahead
        )
        logger.info("[paper] %s %s resting maker @ %.6g (bid_ratio=%.2f, queue_ahead=%.4f)",
                    coin, sig.signal, px, sig.bid_ratio, queue_ahead)

    @staticmethod
    def _resting_size_at_top(levels: list[list[dict[str, Any]]], is_buy: bool) -> float:
        """Total resting size at the best level on our side (bids for a long,
        asks for a short) — the FIFO queue we join behind."""
        side = levels[0] if is_buy else levels[1]
        if not side:
            return 0.0
        try:
            return float(side[0]["sz"])
        except (KeyError, TypeError, ValueError):
            return 0.0

    # --- fills / journal -------------------------------------------------------
    def _open_from(self, order: PendingOrder, entry_fee_rate: float = MAKER_FEE) -> None:
        is_long = order.side == BUY_LONG
        if is_long:
            sl_px = order.px * (1 - STOP_LOSS_PCT)
            tp_px = order.px * (1 + TAKE_PROFIT_PCT)
        else:
            sl_px = order.px * (1 + STOP_LOSS_PCT)
            tp_px = order.px * (1 - TAKE_PROFIT_PCT)

        trade_id = self.journal.record_entry(
            mode="paper",
            strategy=self.strategy,
            coin=order.coin,
            side="long" if is_long else "short",
            signal=order.side,
            entry_px=order.px,
            size=order.size,
            bid_ratio=order.bid_ratio,
            spread_pct=order.spread_pct,
            funding=self.funding_map.get(order.coin),
            sl_px=sl_px,
            tp_px=tp_px,
        )
        self._positions[order.coin] = PaperPosition(
            trade_id, order.coin, is_long, order.px, order.size, sl_px, tp_px,
            opened_at=time.time(), entry_fee_rate=entry_fee_rate, peak_px=order.px,
        )
        logger.info("[paper] FILLED %s %s @ %.6g (sl=%.6g tp=%.6g, entry_fee=%.3f%%)",
                    order.coin, order.side, order.px, sl_px, tp_px, entry_fee_rate * 100)

    def _close(self, pos: PaperPosition, exit_px: float, reason: str, exit_fee_rate: float) -> None:
        gross = (exit_px - pos.entry_px) * pos.size if pos.is_long else (pos.entry_px - exit_px) * pos.size
        entry_fee = pos.entry_px * pos.size * pos.entry_fee_rate  # maker or taker per fill
        exit_fee = exit_px * pos.size * exit_fee_rate
        fees = entry_fee + exit_fee
        pnl = gross - fees
        self.equity += pnl
        self.journal.record_exit(
            pos.trade_id,
            exit_px=exit_px,
            exit_reason=reason,
            fees=fees,
            realized_pnl=pnl,
            equity_after=self.equity,
        )
        del self._positions[pos.coin]
        logger.info("[paper] CLOSE %s %s @ %.6g pnl=$%.4f (%s) equity=$%.4f",
                    pos.coin, "long" if pos.is_long else "short", exit_px, pnl, reason, self.equity)

    # --- funding accrual -------------------------------------------------------
    def accrue_funding(self, now: float | None = None) -> None:
        """Debit/credit funding on open positions once per funding hour.

        Hyperliquid charges funding hourly at the top of the UTC hour. We apply
        it the first time we observe a new UTC hour: payment = notional * rate;
        a long pays when the rate is positive (debit), a short receives (credit).
        `funding_map` already tracks each coin's current hourly rate. This is an
        approximation — it uses the latest known rate and entry notional rather
        than reconstructing the exact per-hour rate history — but it captures the
        directional carry cost of holding across funding stamps.
        """
        now = now if now is not None else time.time()
        hour = int(datetime.fromtimestamp(now, tz=timezone.utc).replace(
            minute=0, second=0, microsecond=0).timestamp())
        with self._lock:
            if self._last_funding_hour is None:
                self._last_funding_hour = hour
                return
            if hour <= self._last_funding_hour:
                return
            self._last_funding_hour = hour
            for pos in self._positions.values():
                rate = self.funding_map.get(pos.coin)
                if not rate:
                    continue
                notional = pos.size * pos.entry_px
                payment = notional * rate  # longs pay when rate > 0
                self.equity += -payment if pos.is_long else payment
                logger.info("[paper] funding %s %s rate=%.6f -> equity %+.4f (equity=$%.4f)",
                            pos.coin, "long" if pos.is_long else "short", rate,
                            (-payment if pos.is_long else payment), self.equity)

    # --- lifecycle -------------------------------------------------------------
    def start(self) -> None:
        for coin in self.coins:
            self._flow[coin] = deque()
            self._sub_ids[coin] = self.info.subscribe({"type": "l2Book", "coin": coin}, self._handle_book)
            self._trade_sub_ids[coin] = self.info.subscribe({"type": "trades", "coin": coin}, self._handle_trades)
            logger.info("[paper] subscribed l2Book + trades %s", coin)

    def stop(self) -> None:
        for coin, sid in self._sub_ids.items():
            try:
                self.info.unsubscribe({"type": "l2Book", "coin": coin}, sid)
            except Exception:  # noqa: BLE001
                pass
        for coin, sid in self._trade_sub_ids.items():
            try:
                self.info.unsubscribe({"type": "trades", "coin": coin}, sid)
            except Exception:  # noqa: BLE001
                pass

    def reconnect(self) -> None:
        """Rebuild a dead/stale WebSocket and resubscribe. Open simulated
        positions are preserved (they live in self._positions, not the socket);
        only the market-data feed is re-established."""
        logger.warning("[paper] WS stale -> reconnecting")
        try:
            self.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.info.disconnect_websocket()
        except Exception:  # noqa: BLE001
            pass
        self._sub_ids.clear()
        self._trade_sub_ids.clear()
        self.info = Info(API_URL, skip_ws=False)
        self.start()
        self._last_msg = time.time()
        logger.info("[paper] reconnected + resubscribed (%d open positions kept)",
                    len(self._positions))


def _refresh_funding(info: Info, coins: list[str]) -> dict[str, float]:
    try:
        meta, ctxs = info.meta_and_asset_ctxs()
        names = [a["name"] for a in meta["universe"]]
        fmap = {}
        for name, ctx in zip(names, ctxs):
            if name in coins:
                try:
                    fmap[name] = float(ctx.get("funding"))
                except (TypeError, ValueError):
                    pass
        return fmap
    except Exception as exc:  # noqa: BLE001
        logger.warning("funding refresh failed: %s", exc)
        return {}


_STOP = threading.Event()


def _request_stop(signum, _frame) -> None:  # noqa: ANN001
    logger.info("[paper] received signal %s -> shutting down", signum)
    _STOP.set()


def _write_status(state: str, engine: "PaperEngine | None" = None, coins: list[str] | None = None,
                  started_at: str | None = None) -> None:
    """Heartbeat the dashboard reads to show run-state / live engine snapshot."""
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "pid": os.getpid(),
            "state": state,                 # 'running' | 'stopped'
            "mode": "paper",
            "strategy": STRATEGY_NAME,
            "network": "mainnet" if config.IS_MAINNET else "testnet",
            "updated_at": _utc_iso(),
        }
        if started_at:
            payload["started_at"] = started_at
        if coins is not None:
            payload["coins"] = coins
        if engine is not None:
            payload["equity"] = round(engine.equity, 4)
            payload["open_positions"] = len(engine._positions)
            payload["pending_orders"] = len(engine._pending)
        STATUS_PATH.write_text(json.dumps(payload, indent=2))
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not write status file: %s", exc)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Graceful shutdown on Ctrl-C (SIGINT) and dashboard stop (SIGTERM).
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    started_at = _utc_iso()
    rest = Info(API_URL, skip_ws=True)
    # Explicit COINS env wins (fixed universe for faster/controlled sampling);
    # otherwise fall back to the volume screener.
    env_coins = os.getenv("COINS")
    if env_coins:
        coins = [c.strip().upper() for c in env_coins.split(",") if c.strip()]
    else:
        coins = run_screener(rest)
    if not coins:
        logger.error("Screener returned no coins.")
        _write_status("stopped")
        return
    logger.info("Paper-trading targets: %s (strategy=%s)", coins, STRATEGY_NAME)

    journal = TradeJournal()
    ws = Info(API_URL, skip_ws=False)
    engine = PaperEngine(ws, coins, journal)
    engine.funding_map = _refresh_funding(rest, coins)
    engine.start()
    _write_status("running", engine, coins, started_at)

    last_funding = time.time()
    last_heartbeat = 0.0
    try:
        while not _STOP.is_set():
            time.sleep(1)
            # WS watchdog: rebuild a dead/stale socket so a sleep/network blip
            # doesn't silently leave the engine blind (no data) but "running".
            if time.time() - engine._last_msg > WS_STALE_SECONDS:
                engine.reconnect()
            engine.accrue_funding()
            if time.time() - last_funding > 300:  # refresh funding every 5 min
                engine.funding_map = _refresh_funding(rest, coins)
                last_funding = time.time()
            if time.time() - last_heartbeat >= 2:  # heartbeat every ~2s
                _write_status("running", engine, coins, started_at)
                last_heartbeat = time.time()
    finally:
        engine.stop()
        ws.disconnect_websocket()
        logger.info("Paper session stats: %s", journal.stats())
        journal.close()
        _write_status("stopped", engine, coins, started_at)


if __name__ == "__main__":
    main()
