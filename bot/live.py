"""Live execution runner (testnet by default).

Ties the screener/coin-universe, the order-book-imbalance strategy, and the
Execution & Risk engine into one continuous loop that places REAL orders on the
venue selected by IS_MAINNET (.env). Every entry/exit is written to the SQLite
journal as mode="live" so the dashboard shows it alongside paper trades.

Flow:
  l2Book stream  -> compute imbalance signal
                 -> when flat + actionable + risk-ok: post-only maker entry
                 -> when holding + imbalance reverts: market-close (signal-flip)
  userFills stream -> on OPEN fill:  journal entry + place reduce-only SL/TP
                   -> on CLOSE fill: journal exit (realized pnl) + cancel sibling
  main loop      -> cancel unfilled entries past the TIF, heartbeat status file

SAFETY: set LIVE_DRY_RUN=true to wire everything up and log intended orders
without sending any. Always validate on testnet (IS_MAINNET=false) first.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

import config
from config import (
    API_URL,
    IS_MAINNET,
    MAIN_ACCOUNT_ADDRESS,
    STRATEGY_NAME,
    WALLET_PRIVATE_KEY,
)
from execution import ExecutionEngine, ORDER_TIF_SECONDS, STOP_LOSS_PCT
from journal import TradeJournal
from screener import run_screener
from strategy import (
    BUY_LONG,
    SELL_SHORT,
    TAKE_PROFIT_PCT,
    compute_signal,
    should_exit,
)

logger = logging.getLogger("live")

STATUS_PATH = Path(__file__).resolve().parent / "data" / "bot_status.json"
# No WS message for this long => treat the socket as dead and rebuild it.
WS_STALE_SECONDS = 60.0
_STOP = threading.Event()


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_stop(signum, _frame) -> None:  # noqa: ANN001
    logger.info("[live] received signal %s -> shutting down", signum)
    _STOP.set()


def select_coins(info: Info) -> list[str]:
    """Trade universe. Explicit COINS env wins. On mainnet, use the screener.
    On testnet the volume screen is meaningless (tiny volume), so default to the
    most liquid testnet perps."""
    env = os.getenv("COINS")
    if env:
        return [c.strip().upper() for c in env.split(",") if c.strip()]
    if IS_MAINNET:
        coins = run_screener(info)
        return coins or ["BTC", "ETH"]
    return ["BTC", "ETH", "SOL", "DOGE"]


class LiveTrader:
    def __init__(self, exec_engine: ExecutionEngine, ws: Info, journal: TradeJournal,
                 coins: list[str], strategy: str = STRATEGY_NAME) -> None:
        self.exec = exec_engine
        self.ws = ws
        self.journal = journal
        self.coins = coins
        self.strategy = strategy
        self.address = exec_engine.master_address

        # coin -> {trade_id, is_long, entry_px, sz, sl_px, tp_px, opened_at}
        self.pos: dict[str, dict[str, Any]] = {}
        # entry context captured on each book tick, used when an open fill lands
        self._ctx: dict[str, dict[str, float]] = {}
        # coin -> ts of a placed-but-unfilled entry (anti-spam guard)
        self._pending: dict[str, float] = {}
        # hybrid entry: coin -> {ts, is_buy} for a resting maker order we may
        # convert to a taker cross if it has not filled within HYBRID_CHASE_SECONDS
        self._chase: dict[str, dict[str, Any]] = {}
        # aggressor-flow buffer per coin: deque of (ts, is_buy, notional)
        self._flow: dict[str, deque[tuple[float, bool, float]]] = {}
        # coins we just market-closed via a flip / trailing stop so the close
        # fill is labelled with the right exit reason
        self._flip: set[str] = set()
        self._trail: set[str] = set()
        self._seen_fills: set[Any] = set()
        self.funding_map: dict[str, float] = {}
        self._sub_ids: dict[str, int] = {}
        self._trade_sub_ids: dict[str, int] = {}
        self._fills_sub: int | None = None
        self._lock = threading.Lock()
        # Only subscribe to the trades stream when a flow feature needs it.
        self._use_flow = config.REQUIRE_FLOW_CONFIRM or config.MIN_FLOW_NOTIONAL_USD > 0
        # Last WS message timestamp; the main loop watches it to rebuild a dead
        # socket (the SDK does not auto-reconnect after a sleep/network drop).
        self._last_msg = time.time()

    # --- subscriptions ---------------------------------------------------------
    def start(self) -> None:
        # Reconcile FIRST so a restart resumes managing live positions before any
        # book tick can mistake a held coin for "flat".
        self.adopt_open_positions()
        self._subscribe()

    def _subscribe(self) -> None:
        """(Re)establish all WS subscriptions on the current self.ws."""
        for coin in self.coins:
            self._sub_ids[coin] = self.ws.subscribe(
                {"type": "l2Book", "coin": coin}, self._on_book
            )
            logger.info("[live] subscribed l2Book %s", coin)
            if self._use_flow:
                self._flow.setdefault(coin, deque())
                self._trade_sub_ids[coin] = self.ws.subscribe(
                    {"type": "trades", "coin": coin}, self._on_trades
                )
                logger.info("[live] subscribed trades %s (flow confirm)", coin)
        self._fills_sub = self.ws.subscribe(
            {"type": "userFills", "user": self.address}, self._on_fills
        )
        logger.info("[live] subscribed userFills %s", self.address)

    def reconnect(self) -> None:
        """Rebuild a dead/stale WebSocket and resubscribe WITHOUT re-adopting
        positions (those are already tracked in self.pos). Protective SL/TP
        orders rest on-chain independently, so only the data feed is rebuilt."""
        logger.warning("[live] WS stale -> reconnecting")
        try:
            self.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.ws.disconnect_websocket()
        except Exception:  # noqa: BLE001
            pass
        self._sub_ids.clear()
        self._trade_sub_ids.clear()
        self._fills_sub = None
        self.ws = Info(API_URL, skip_ws=False)
        self._subscribe()
        self._last_msg = time.time()
        logger.info("[live] reconnected + resubscribed (%d open positions kept)", len(self.pos))

    def adopt_open_positions(self) -> None:
        """Resume managing positions that already exist on-chain (e.g. after a
        stop/restart). Each live position is matched to an open journal row for
        the same coin so its eventual close is journaled to the right trade; a
        reconstructed entry is created if no open row exists. Protective SL/TP
        orders are (re)placed only if none are currently resting."""
        try:
            state = self.exec.fetch_state()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[live] reconcile: could not fetch state (%s)", exc)
            return
        live_positions = [ap.get("position", {}) for ap in state.get("assetPositions", [])]
        live_positions = [p for p in live_positions if float(p.get("szi", 0) or 0) != 0]
        if not live_positions:
            return

        open_rows: dict[str, dict[str, Any]] = {}
        for r in self.journal.open_trades():
            if r.get("mode") == "live":
                open_rows[r["coin"]] = r  # most-recent open row per coin
        resting: dict[str, bool] = {}
        try:
            for o in self.exec.info.open_orders(self.address):
                resting[o.get("coin")] = True
        except Exception:  # noqa: BLE001
            pass

        for p in live_positions:
            coin = p.get("coin")
            if not coin or coin not in self.coins:
                continue
            szi = float(p.get("szi", 0) or 0)
            is_long = szi > 0
            entry_px = float(p.get("entryPx") or 0)
            sz = abs(szi)
            if is_long:
                sl_px = self.exec.round_price(coin, entry_px * (1 - STOP_LOSS_PCT))
                tp_px = self.exec.round_price(coin, entry_px * (1 + TAKE_PROFIT_PCT))
            else:
                sl_px = self.exec.round_price(coin, entry_px * (1 + STOP_LOSS_PCT))
                tp_px = self.exec.round_price(coin, entry_px * (1 - TAKE_PROFIT_PCT))

            row = open_rows.get(coin)
            if row is not None:
                trade_id = int(row["id"])
                sl_px = row.get("sl_px") or sl_px
                tp_px = row.get("tp_px") or tp_px
            else:
                trade_id = self.journal.record_entry(
                    mode="live", strategy=self.strategy, coin=coin,
                    side="long" if is_long else "short",
                    signal=BUY_LONG if is_long else SELL_SHORT,
                    entry_px=entry_px, size=sz, sl_px=sl_px, tp_px=tp_px,
                )
            self.pos[coin] = {
                "trade_id": trade_id, "is_long": is_long,
                "entry_px": entry_px, "sz": sz, "sl_px": sl_px, "tp_px": tp_px,
                # adopted positions are already past any min-hold window
                "opened_at": 0.0, "peak_px": entry_px,
            }
            if not resting.get(coin):
                self.exec.place_protective_orders(coin, is_long, entry_px, sz)
            logger.info("[live] adopted open %s %s sz=%s @ %s (trade_id=%s, protected=%s)",
                        coin, "long" if is_long else "short", sz, entry_px,
                        trade_id, bool(resting.get(coin)))

    def stop(self) -> None:
        for coin, sid in self._sub_ids.items():
            try:
                self.ws.unsubscribe({"type": "l2Book", "coin": coin}, sid)
            except Exception:  # noqa: BLE001
                pass
        for coin, sid in self._trade_sub_ids.items():
            try:
                self.ws.unsubscribe({"type": "trades", "coin": coin}, sid)
            except Exception:  # noqa: BLE001
                pass
        if self._fills_sub is not None:
            try:
                self.ws.unsubscribe({"type": "userFills", "user": self.address}, self._fills_sub)
            except Exception:  # noqa: BLE001
                pass

    # --- order-book driven entries / flip exits --------------------------------
    def _on_book(self, msg: dict[str, Any]) -> None:
        if msg.get("channel") != "l2Book":
            return
        self._last_msg = time.time()
        data = msg.get("data", {})
        coin = data.get("coin")
        levels = data.get("levels")
        if not coin or coin not in self.coins:
            return
        sig = compute_signal(coin, levels)
        if sig is None:
            return

        # capture entry context for journaling
        spread_pct = self._spread_pct(levels)
        self._ctx[coin] = {
            "bid_ratio": sig.bid_ratio,
            "spread_pct": spread_pct if spread_pct is not None else float("nan"),
            "funding": self.funding_map.get(coin, float("nan")),
        }

        with self._lock:
            holding = self.pos.get(coin)
            if holding:
                direction = BUY_LONG if holding["is_long"] else SELL_SHORT
                held_for = time.time() - holding.get("opened_at", 0.0)
                busy = coin in self._flip or coin in self._trail
                # obi_v4 trailing stop: ride behind the best favorable price and
                # market-close on a retrace (the hard SL/TP bracket rests on-chain).
                if not busy and self._trailing_hit(holding, levels):
                    self._trail_close(coin, holding)
                    return
                # MIN_HOLD_SECONDS guards only the signal-flip exit, never the
                # protective SL/TP bracket (those rest on-chain independently).
                if (held_for >= config.MIN_HOLD_SECONDS
                        and should_exit(direction, sig.bid_ratio)
                        and not busy):
                    self._flip_close(coin, holding)
                return
            if sig.signal not in (BUY_LONG, SELL_SHORT):
                return
            # anti-spam: don't stack entries while one is resting unfilled
            pend = self._pending.get(coin)
            if pend and time.time() - pend < ORDER_TIF_SECONDS:
                return
            # entry gates: skip wide spreads / unconfirmed or thin aggressor flow
            if not self._entry_allowed(coin, sig.signal, spread_pct):
                return
            is_buy = sig.signal == BUY_LONG
            # hybrid: rest as a maker first, then chase with a taker if unfilled
            initial_mode = "maker" if config.ENTRY_MODE == "hybrid" else config.ENTRY_MODE
            placed = self.exec.submit_entry(coin, is_buy=is_buy, mode=initial_mode)
            if placed is not None:
                now = time.time()
                self._pending[coin] = now
                if config.ENTRY_MODE == "hybrid":
                    self._chase[coin] = {"ts": now, "is_buy": is_buy}

    # --- aggressor-flow buffer + entry gates -----------------------------------
    def _on_trades(self, msg: dict[str, Any]) -> None:
        """Buffer recent public trades per coin so we can measure realized
        aggressor flow (who is actually crossing the spread), not just resting
        book depth. Only active when a flow feature is enabled."""
        if msg.get("channel") != "trades":
            return
        now = time.time()
        self._last_msg = now
        cutoff = now - config.FLOW_WINDOW_SECONDS
        for t in msg.get("data", []) or []:
            buf = self._flow.get(t.get("coin"))
            if buf is None:
                continue
            try:
                notional = float(t.get("px", 0) or 0) * float(t.get("sz", 0) or 0)
            except (TypeError, ValueError):
                continue
            # HL trade side: "B" = buy aggressor (lifted ask), "A" = sell aggressor.
            buf.append((now, t.get("side") == "B", notional))
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

    def _entry_allowed(self, coin: str, signal: str, spread_pct: float | None) -> bool:
        """Pre-entry gates. Returns False to skip the entry."""
        # Spread gate: a wide spread means the round-trip cost eats the edge.
        if spread_pct is not None and spread_pct > config.MAX_ENTRY_SPREAD_PCT:
            return False
        if self._use_flow:
            buy_n, sell_n = self._recent_flow(coin)
            total = buy_n + sell_n
            # Liquidity/activity floor: skip dead, illiquid, whippy windows.
            if total < config.MIN_FLOW_NOTIONAL_USD:
                return False
            # Direction confirmation: realized flow must agree with the signal.
            if config.REQUIRE_FLOW_CONFIRM and total > 0:
                want_buy = signal == BUY_LONG
                agree = (buy_n if want_buy else sell_n) / total
                if agree < config.FLOW_CONFIRM_RATIO:
                    return False
        return True

    def process_hybrid_chase(self, now: float) -> None:
        """For ENTRY_MODE=hybrid: convert a post-only entry that has rested longer
        than HYBRID_CHASE_SECONDS (and is still unfilled) into a taker cross."""
        if not self._chase:
            return
        with self._lock:
            for coin, info in list(self._chase.items()):
                if coin in self.pos:           # maker already filled
                    self._chase.pop(coin, None)
                    continue
                if now - info["ts"] < config.HYBRID_CHASE_SECONDS:
                    continue
                self._chase.pop(coin, None)
                self.exec.cancel_coin_orders(coin)  # drop the unfilled maker
                placed = self.exec.submit_entry(coin, is_buy=info["is_buy"], mode="taker")
                if placed is not None:
                    self._pending[coin] = time.time()
                    logger.info("[live] hybrid chase: crossing %s as taker (maker unfilled)", coin)

    def _trailing_hit(self, holding: dict[str, Any], levels: list[list[dict[str, Any]]]) -> bool:
        """obi_v4 trailing stop. Updates the position's high-/low-water mark from
        the current book and returns True once price has run TRAIL_ACTIVATE_PCT
        past entry and then retraced TRAIL_PCT behind that extreme. Off when
        TRAIL_PCT <= 0."""
        if not levels or len(levels) < 2 or not levels[0] or not levels[1]:
            return False
        try:
            best_bid = float(levels[0][0]["px"])
            best_ask = float(levels[1][0]["px"])
        except (KeyError, IndexError, TypeError, ValueError):
            return False
        entry = holding.get("entry_px", 0.0)
        if entry <= 0:
            return False
        if config.TRAIL_PCT <= 0:
            return False
        if holding["is_long"]:
            peak = max(holding.get("peak_px", entry), best_bid)
            holding["peak_px"] = peak
            armed = (peak - entry) / entry >= config.TRAIL_ACTIVATE_PCT
            return armed and best_bid <= peak * (1 - config.TRAIL_PCT)
        peak = min(holding.get("peak_px", entry), best_ask)
        holding["peak_px"] = peak
        armed = (entry - peak) / entry >= config.TRAIL_ACTIVATE_PCT
        return armed and best_ask >= peak * (1 + config.TRAIL_PCT)

    def _trail_close(self, coin: str, holding: dict[str, Any]) -> None:
        logger.info("[live] trailing-stop exit -> market-close %s", coin)
        self._trail.add(coin)
        self.exec._submit(  # noqa: SLF001 - intentional reuse of gated submit
            f"[trail] market-close {coin}",
            lambda: self.exec.exchange.market_close(coin),
        )

    def _flip_close(self, coin: str, holding: dict[str, Any]) -> None:
        logger.info("[live] signal-flip exit -> market-close %s", coin)
        self._flip.add(coin)
        self.exec._submit(  # noqa: SLF001 - intentional reuse of gated submit
            f"[flip] market-close {coin}",
            lambda: self.exec.exchange.market_close(coin),
        )

    # --- fill driven journaling / protective orders ----------------------------
    def _on_fills(self, msg: dict[str, Any]) -> None:
        if msg.get("channel") != "userFills":
            return
        self._last_msg = time.time()
        data = msg.get("data", {})
        if data.get("isSnapshot"):
            return  # historical fills on (re)subscribe — ignore
        for f in data.get("fills", []):
            coin = f.get("coin")
            if coin not in self.coins:
                continue
            tid = f.get("tid")
            if tid in self._seen_fills:
                continue
            self._seen_fills.add(tid)
            direction = f.get("dir", "")
            px = float(f.get("px", 0) or 0)
            sz = float(f.get("sz", 0) or 0)
            fee = float(f.get("fee", 0) or 0)
            closed = float(f.get("closedPnl", 0) or 0)
            if "Open" in direction:
                self._on_open_fill(coin, "Long" in direction, px, sz, fee)
            elif "Close" in direction:
                self._on_close_fill(coin, px, sz, closed, fee)

    def _on_open_fill(self, coin: str, is_long: bool, px: float, sz: float, fee: float) -> None:
        with self._lock:
            self._pending.pop(coin, None)
            if coin in self.pos:
                return  # already tracked (partial fills add to same position)
            if is_long:
                sl_px = self.exec.round_price(coin, px * (1 - STOP_LOSS_PCT))
                tp_px = self.exec.round_price(coin, px * (1 + TAKE_PROFIT_PCT))
            else:
                sl_px = self.exec.round_price(coin, px * (1 + STOP_LOSS_PCT))
                tp_px = self.exec.round_price(coin, px * (1 - TAKE_PROFIT_PCT))
            ctx = self._ctx.get(coin, {})
            trade_id = self.journal.record_entry(
                mode="live",
                strategy=self.strategy,
                coin=coin,
                side="long" if is_long else "short",
                signal=BUY_LONG if is_long else SELL_SHORT,
                entry_px=px,
                size=sz,
                bid_ratio=_clean(ctx.get("bid_ratio")),
                spread_pct=_clean(ctx.get("spread_pct")),
                funding=_clean(ctx.get("funding")),
                sl_px=sl_px,
                tp_px=tp_px,
            )
            self.pos[coin] = {
                "trade_id": trade_id, "is_long": is_long,
                "entry_px": px, "sz": sz, "sl_px": sl_px, "tp_px": tp_px,
                "opened_at": time.time(), "peak_px": px,
            }
            self._chase.pop(coin, None)  # filled — no taker chase needed
        logger.info("[live] OPEN %s %s sz=%s @ %s (sl=%s tp=%s)",
                    coin, "long" if is_long else "short", sz, px, sl_px, tp_px)
        # Place reduce-only protective orders for the filled size.
        self.exec.place_protective_orders(coin, is_long, px, sz)

    def _on_close_fill(self, coin: str, px: float, sz: float, closed: float, fee: float) -> None:
        with self._lock:
            holding = self.pos.pop(coin, None)
            flip = coin in self._flip
            trail = coin in self._trail
            self._flip.discard(coin)
            self._trail.discard(coin)
        if holding is None:
            return
        reason = self._exit_reason(holding, px, closed, flip, trail)
        equity_after = self._account_value()
        self.journal.record_exit(
            holding["trade_id"], exit_px=px, exit_reason=reason,
            realized_pnl=closed, fees=fee, equity_after=equity_after,
        )
        logger.info("[live] CLOSE %s @ %s pnl=%.4f reason=%s", coin, px, closed, reason)
        # Cancel the sibling protective order left resting after the close.
        self.exec.cancel_coin_orders(coin)

    @staticmethod
    def _exit_reason(holding: dict[str, Any], px: float, closed: float, flip: bool,
                     trail: bool = False) -> str:
        if trail:
            return "trailing_stop"
        if flip:
            return "signal_flip"
        sl, tp = holding.get("sl_px"), holding.get("tp_px")
        if sl and tp:
            # nearest trigger wins; pnl sign breaks ties
            if abs(px - tp) <= abs(px - sl):
                return "take_profit"
            return "stop_loss"
        return "take_profit" if closed >= 0 else "stop_loss"

    # --- helpers ---------------------------------------------------------------
    def _account_value(self) -> float:
        try:
            return self.exec.account_equity()
        except Exception:  # noqa: BLE001
            return 0.0

    @staticmethod
    def _spread_pct(levels: list[list[dict[str, Any]]] | None) -> float | None:
        if not levels or len(levels) < 2 or not levels[0] or not levels[1]:
            return None
        try:
            bid = float(levels[0][0]["px"])
            ask = float(levels[1][0]["px"])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        return ((ask - bid) / ask) * 100.0 if ask > 0 else None


def _clean(v: float | None) -> float | None:
    if v is None or (isinstance(v, float) and v != v):  # NaN check
        return None
    return float(v)


def _refresh_funding(info: Info, coins: list[str]) -> dict[str, float]:
    try:
        meta, ctxs = info.meta_and_asset_ctxs()
        names = [a["name"] for a in meta["universe"]]
        out: dict[str, float] = {}
        for name, ctx in zip(names, ctxs):
            if name in coins:
                try:
                    out[name] = float(ctx.get("funding"))
                except (TypeError, ValueError):
                    pass
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("funding refresh failed: %s", exc)
        return {}


def _write_status(state: str, trader: "LiveTrader | None", equity: float | None,
                  coins: list[str] | None, started_at: str | None, dry_run: bool) -> None:
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "pid": os.getpid(),
            "state": state,
            "mode": "live-dry" if dry_run else "live",
            "strategy": STRATEGY_NAME,
            "network": "mainnet" if IS_MAINNET else "testnet",
            "updated_at": _utc_iso(),
        }
        if started_at:
            payload["started_at"] = started_at
        if coins is not None:
            payload["coins"] = coins
        if equity is not None:
            payload["equity"] = round(equity, 4)
        if trader is not None:
            payload["open_positions"] = len(trader.pos)
        STATUS_PATH.write_text(json.dumps(payload, indent=2))
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not write status file: %s", exc)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    if not WALLET_PRIVATE_KEY or not MAIN_ACCOUNT_ADDRESS:
        logger.error("WALLET_PRIVATE_KEY and MAIN_ACCOUNT_ADDRESS must be set in .env")
        return

    dry_run = os.getenv("LIVE_DRY_RUN", "false").strip().lower() == "true"
    started_at = _utc_iso()
    net = "mainnet" if IS_MAINNET else "testnet"
    logger.info("[live] starting on %s (dry_run=%s, strategy=%s)", net, dry_run, STRATEGY_NAME)
    if IS_MAINNET and not dry_run:
        logger.warning("[live] MAINNET + live orders: trading REAL funds")

    rest = Info(API_URL, skip_ws=True)
    coins = select_coins(rest)
    if not coins:
        logger.error("[live] no coins selected; exiting")
        _write_status("stopped", None, None, None, started_at, dry_run)
        return
    logger.info("[live] universe: %s", coins)

    agent = eth_account.Account.from_key(WALLET_PRIVATE_KEY)
    exchange = Exchange(agent, API_URL, account_address=MAIN_ACCOUNT_ADDRESS)
    exec_engine = ExecutionEngine(exchange, rest, MAIN_ACCOUNT_ADDRESS, dry_run=dry_run)

    margin = exec_engine.available_margin()
    logger.info("[live] free margin: $%.2f", margin)
    if not dry_run and margin < config.ORDER_NOTIONAL_USD:
        logger.error("[live] insufficient margin ($%.2f < $%.2f order). Fund the PERPS "
                     "account (transfer.py spot-to-perp) before live trading.",
                     margin, config.ORDER_NOTIONAL_USD)
        _write_status("stopped", None, margin, coins, started_at, dry_run)
        return

    journal = TradeJournal()
    ws = Info(API_URL, skip_ws=False)
    trader = LiveTrader(exec_engine, ws, journal, coins)
    trader.funding_map = _refresh_funding(rest, coins)
    trader.start()
    _write_status("running", trader, exec_engine.account_equity(), coins, started_at, dry_run)

    last_funding = time.time()
    last_status = 0.0
    last_equity = margin
    try:
        while not _STOP.is_set():
            time.sleep(1)
            now = time.time()
            # WS watchdog: rebuild a dead/stale socket (host slept, network blip)
            # so the bot doesn't sit blind while holding live positions.
            if now - trader._last_msg > WS_STALE_SECONDS:
                trader.reconnect()
            # hybrid: cross any post-only entry that has not filled in time
            trader.process_hybrid_chase(now)
            exec_engine.cancel_stale_orders()
            # drop stale pending-entry guards (their resting order has TIF-cancelled)
            for c, ts in list(trader._pending.items()):
                if now - ts >= ORDER_TIF_SECONDS:
                    trader._pending.pop(c, None)
            if now - last_funding > 300:
                trader.funding_map = _refresh_funding(rest, coins)
                last_funding = now
            if now - last_status >= 3:
                last_equity = exec_engine.account_equity() or last_equity
                _write_status("running", trader, last_equity, coins, started_at, dry_run)
                last_status = now
    finally:
        trader.stop()
        ws.disconnect_websocket()
        logger.info("[live] session stats: %s", journal.stats())
        journal.close()
        _write_status("stopped", trader, last_equity, coins, started_at, dry_run)


if __name__ == "__main__":
    main()
