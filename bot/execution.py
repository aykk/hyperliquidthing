"""Execution & Risk Engine.

Consumes signals from the Strategy Engine, runs hard risk checks against LIVE
exchange state, and routes isolated, post-only orders with stop-loss / take-profit
protection. Includes an emergency flatten routine.

SAFETY: defaults to DRY_RUN=True. In dry-run it queries real state and logs every
order it *would* send, but submits nothing. Set dry_run=False (and ideally point
at testnet via IS_MAINNET=false) only when you are ready to trade real funds.

Spec: specs/strategy_execution.md (section 3), specs/spec.md (global constraints)
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field

import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

import config
from config import API_URL, MAIN_ACCOUNT_ADDRESS, WALLET_PRIVATE_KEY
from strategy import BUY_LONG, SELL_SHORT, SignalResult, TAKE_PROFIT_PCT

logger = logging.getLogger("execution")

# --- Risk parameters (specs/spec.md + strategy_execution.md) -------------------
# Sizing knobs are env-configurable (see bot/config.py). Defaults assume a $250
# per-strategy budget: $25 notional x up to 5 positions = $125 max exposure.
ORDER_NOTIONAL_USD = config.ORDER_NOTIONAL_USD        # notional per trade (>= $10 protocol min)
MAX_CONCURRENT_POSITIONS = config.MAX_CONCURRENT_POSITIONS  # caps total exposure
MAX_ORDERS_PER_HOUR = config.MAX_ORDERS_PER_HOUR  # API write budget protection
ORDER_TIF_SECONDS = 15          # cancel a resting entry if unfilled this long
STOP_LOSS_PCT = config.STOP_LOSS_PCT  # hard stop from entry (env-overridable; default 1.5%)
LEVERAGE = 1                    # 1x isolated -> notional uses equal margin

POST_ONLY: dict = {"limit": {"tif": "Alo"}}  # Alo = Add Liquidity Only (maker-only)
IOC: dict = {"limit": {"tif": "Ioc"}}        # Ioc = Immediate-or-Cancel (marketable taker)

# Entry execution model + taker slippage cap (see bot/config.py).
ENTRY_MODE = config.ENTRY_MODE
MAX_TAKER_SLIPPAGE_PCT = config.MAX_TAKER_SLIPPAGE_PCT


class RateLimiter:
    """Sliding-window limiter for API write actions (orders/cancels)."""

    def __init__(self, max_actions: int, window_seconds: int = 3600) -> None:
        self.max_actions = max_actions
        self.window_seconds = window_seconds
        self._timestamps: list[float] = []

    def _prune(self) -> None:
        cutoff = time.time() - self.window_seconds
        self._timestamps = [t for t in self._timestamps if t > cutoff]

    def allow(self) -> bool:
        self._prune()
        return len(self._timestamps) < self.max_actions

    def record(self) -> None:
        self._timestamps.append(time.time())

    @property
    def used(self) -> int:
        self._prune()
        return len(self._timestamps)


@dataclass
class ExecutionEngine:
    exchange: Exchange
    info: Info
    master_address: str
    dry_run: bool = True
    rate_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(MAX_ORDERS_PER_HOUR))
    _leverage_set: set[str] = field(default_factory=set)
    _mode: str | None = None

    # --- price/size rounding (Hyperliquid: 5 sig figs, 6-szDecimals dp for perps)
    def _sz_decimals(self, coin: str) -> int:
        return self.info.asset_to_sz_decimals[self.info.name_to_asset(coin)]

    def round_size(self, coin: str, sz: float) -> float:
        return round(sz, self._sz_decimals(coin))

    def size_for_notional(self, coin: str, notional: float, px: float) -> float:
        """Size rounded UP to the lot increment so the order clears the $10
        minimum notional (rounding down can fall under the protocol minimum)."""
        decimals = self._sz_decimals(coin)
        step = 10 ** (-decimals)
        lots = math.ceil((notional / px) / step)
        return round(lots * step, decimals)

    def round_price(self, coin: str, px: float) -> float:
        return round(float(f"{px:.5g}"), 6 - self._sz_decimals(coin))

    def best_bid_ask(self, coin: str) -> tuple[float, float] | None:
        snap = self.info.l2_snapshot(coin)
        levels = snap.get("levels") if isinstance(snap, dict) else None
        if not levels or len(levels) < 2 or not levels[0] or not levels[1]:
            return None
        return float(levels[0][0]["px"]), float(levels[1][0]["px"])

    # --- account abstraction mode (unified / portfolio / standard) --------------
    def account_mode(self) -> str:
        """Returns 'unifiedAccount', 'portfolioMargin', or 'manual'/'standard'.
        Cached after first lookup."""
        if self._mode is None:
            try:
                self._mode = self.info.post(
                    "/info", {"type": "userAbstraction", "user": self.master_address}
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not query userAbstraction (%s); assuming standard", exc)
                self._mode = "manual"
        return self._mode

    @property
    def _is_unified(self) -> bool:
        return self.account_mode() in ("unifiedAccount", "portfolioMargin")

    # --- LIVE state verification (never trust local state for positions) --------
    def fetch_state(self) -> dict:
        return self.info.user_state(self.master_address)

    def open_positions(self, state: dict | None = None) -> dict[str, float]:
        state = state or self.fetch_state()
        out: dict[str, float] = {}
        for ap in state.get("assetPositions", []):
            pos = ap.get("position", {})
            szi = float(pos.get("szi", 0) or 0)
            if szi != 0:
                out[pos["coin"]] = szi
        return out

    def available_margin(self, state: dict | None = None) -> float:
        """Free collateral available for a new order.

        In unified / portfolio-margin mode, clearinghouseState (perps) is NOT the
        source of truth and reports 0 — the spot clearinghouse state holds the real
        unified USDC balance. Fall back to clearinghouseState.withdrawable only in
        standard mode.
        """
        if self._is_unified:
            spot = self.info.spot_user_state(self.master_address)
            for bal in spot.get("balances", []):
                if bal.get("coin") == "USDC":
                    return float(bal.get("total", 0) or 0) - float(bal.get("hold", 0) or 0)
            return 0.0
        state = state or self.fetch_state()
        return float(state.get("withdrawable", 0) or 0)

    def account_equity(self, state: dict | None = None) -> float:
        """Total mark-to-market account equity for display/journal.

        Unified / portfolio accounts hold ONE collateral pool reported as the
        spot USDC balance. When a position opens, its margin is *reserved from*
        that pool (the spot total only drops by fees, not by the margin), and the
        perps `accountValue` mirrors that reserved margin + unrealized PnL. So
        adding spot + perps accountValue double-counts the margin. Correct
        unified equity = spot USDC + open unrealized PnL. Standard accounts use
        the perps accountValue alone.
        """
        state = state or self.fetch_state()
        perps_val = float(state.get("marginSummary", {}).get("accountValue", 0) or 0)
        if not self._is_unified:
            return perps_val
        spot = self.info.spot_user_state(self.master_address)
        spot_usdc = next(
            (float(b.get("total", 0) or 0) for b in spot.get("balances", [])
             if b.get("coin") == "USDC"),
            0.0,
        )
        upnl = sum(
            float(ap.get("position", {}).get("unrealizedPnl", 0) or 0)
            for ap in state.get("assetPositions", [])
        )
        return spot_usdc + upnl

    # --- risk gate --------------------------------------------------------------
    def _passes_risk_checks(self, coin: str, state: dict) -> bool:
        positions = self.open_positions(state)
        if coin in positions:
            logger.info("[risk] already holding %s; skipping new entry", coin)
            return False
        if len(positions) >= MAX_CONCURRENT_POSITIONS:
            logger.info("[risk] at max concurrent positions (%d); skipping", len(positions))
            return False
        if not self.rate_limiter.allow():
            logger.warning("[risk] order rate limit reached (%d/hr); skipping", MAX_ORDERS_PER_HOUR)
            return False
        if self.available_margin(state) < ORDER_NOTIONAL_USD:
            logger.warning("[risk] insufficient free margin for $%.2f order; skipping", ORDER_NOTIONAL_USD)
            return False
        return True

    # --- order submission (gated by dry_run) ------------------------------------
    def _submit(self, description: str, fn) -> dict | None:
        if self.dry_run:
            logger.info("[DRY_RUN] would submit: %s", description)
            return None
        result = fn()
        self.rate_limiter.record()
        logger.info("submitted: %s -> %s", description, result)
        return result

    def _ensure_leverage(self, coin: str) -> None:
        """Set per-coin leverage/margin mode once.

        Unified / portfolio accounts share a single collateral pool, so cross
        margin is the natural fit: all positions draw from the same balance and
        equity reads correctly. Isolated margin on a unified account fragments
        the pool into per-coin buckets and makes the perps equity read as a tiny
        sliver. Standard accounts keep isolated margin per the original spec.
        """
        if coin in self._leverage_set:
            return
        use_cross = self._is_unified
        mode = "CROSS" if use_cross else "ISOLATED"
        desc = f"set {coin} to {LEVERAGE}x {mode}"
        if self.dry_run:
            logger.info("[DRY_RUN] would %s", desc)
        else:
            self.exchange.update_leverage(LEVERAGE, coin, is_cross=use_cross)
            logger.info("%s", desc)
        self._leverage_set.add(coin)

    def submit_entry(self, coin: str, is_buy: bool, mode: str | None = None) -> dict | None:
        """Run risk checks and place a single entry. `mode` overrides ENTRY_MODE
        for this order: "maker" rests a post-only Alo order at the passive side;
        "taker" places a marketable IOC that crosses the spread. (The "hybrid"
        mode is orchestrated by the live runner, which calls this with an explicit
        "maker" then "taker".) Returns {coin, is_buy, px, sz, notional, result}
        when an order was placed (or would be, in dry-run), else None. Protective
        SL/TP are placed separately on fill confirmation."""
        effective_mode = (mode or ENTRY_MODE)
        state = self.fetch_state()  # LIVE state, every time
        if not self._passes_risk_checks(coin, state):
            return None

        quotes = self.best_bid_ask(coin)
        if quotes is None:
            logger.warning("[%s] no book snapshot; skipping", coin)
            return None
        best_bid, best_ask = quotes

        if effective_mode == "taker":
            # Marketable IOC: cross the spread to fill immediately, capping how
            # far past the touch we'll pay/sell (MAX_TAKER_SLIPPAGE_PCT) so a thin
            # book can't fill us at a runaway price. Real fill price arrives via
            # userFills; protective SL/TP are computed off that actual fill.
            ref_px = best_ask if is_buy else best_bid
            limit_px = self.round_price(
                coin,
                best_ask * (1 + MAX_TAKER_SLIPPAGE_PCT) if is_buy
                else best_bid * (1 - MAX_TAKER_SLIPPAGE_PCT),
            )
            sz = self.size_for_notional(coin, ORDER_NOTIONAL_USD, ref_px)
            order_type, label, entry_px = IOC, "taker-IOC", ref_px
        else:
            # Maker post-only at the passive side so we rest as a maker.
            entry_px = self.round_price(coin, best_bid if is_buy else best_ask)
            limit_px = entry_px
            sz = self.size_for_notional(coin, ORDER_NOTIONAL_USD, entry_px)
            order_type, label = POST_ONLY, "post-only"

        notional = sz * entry_px
        self._ensure_leverage(coin)
        result = self._submit(
            f"{'BUY_LONG' if is_buy else 'SELL_SHORT'} {coin} sz={sz} @ {limit_px} "
            f"({label}, ${notional:.2f})",
            lambda: self.exchange.order(coin, is_buy, sz, limit_px, order_type),
        )
        self._log_protective_targets(coin, is_buy, entry_px, sz)
        return {"coin": coin, "is_buy": is_buy, "px": limit_px, "sz": sz,
                "notional": notional, "result": result}

    def handle_signal(self, signal: SignalResult) -> None:
        if signal.signal not in (BUY_LONG, SELL_SHORT):
            return
        self.submit_entry(signal.coin, is_buy=signal.signal == BUY_LONG)

    def _log_protective_targets(self, coin: str, is_buy: bool, entry_px: float, sz: float) -> None:
        if is_buy:
            sl_px = self.round_price(coin, entry_px * (1 - STOP_LOSS_PCT))
            tp_px = self.round_price(coin, entry_px * (1 + TAKE_PROFIT_PCT))
        else:
            sl_px = self.round_price(coin, entry_px * (1 + STOP_LOSS_PCT))
            tp_px = self.round_price(coin, entry_px * (1 - TAKE_PROFIT_PCT))
        logger.info("[%s] on fill -> stop-loss @ %s, take-profit @ %s", coin, sl_px, tp_px)

    def place_protective_orders(self, coin: str, is_long: bool, entry_px: float, sz: float) -> None:
        """Submit reduce-only stop-loss (market trigger) and take-profit (maker
        limit trigger) after an entry fill. Call this from a fill handler."""
        close_is_buy = not is_long
        if is_long:
            sl_px = self.round_price(coin, entry_px * (1 - STOP_LOSS_PCT))
            tp_px = self.round_price(coin, entry_px * (1 + TAKE_PROFIT_PCT))
        else:
            sl_px = self.round_price(coin, entry_px * (1 + STOP_LOSS_PCT))
            tp_px = self.round_price(coin, entry_px * (1 - TAKE_PROFIT_PCT))

        sl_type = {"trigger": {"triggerPx": sl_px, "isMarket": True, "tpsl": "sl"}}
        tp_type = {"trigger": {"triggerPx": tp_px, "isMarket": False, "tpsl": "tp"}}
        self._submit(
            f"stop-loss {coin} sz={sz} trigger@{sl_px} (reduce-only market)",
            lambda: self.exchange.order(coin, close_is_buy, sz, sl_px, sl_type, reduce_only=True),
        )
        self._submit(
            f"take-profit {coin} sz={sz} trigger@{tp_px} (reduce-only maker)",
            lambda: self.exchange.order(coin, close_is_buy, sz, tp_px, tp_type, reduce_only=True),
        )

    def cancel_coin_orders(self, coin: str) -> None:
        """Cancel all resting orders for one coin (e.g. the leftover protective
        order after the position is closed by its sibling)."""
        for o in self.info.open_orders(self.master_address):
            if o.get("coin") == coin:
                self._submit(
                    f"cancel {coin} oid={o['oid']}",
                    lambda o=o: self.exchange.cancel(o["coin"], o["oid"]),
                )

    def cancel_stale_orders(self) -> None:
        """Cancel unfilled ENTRY orders older than ORDER_TIF_SECONDS.

        CRITICAL: never cancel reduce-only protective orders (stop-loss /
        take-profit) — those must rest until the position closes. We use
        frontend_open_orders (which exposes reduceOnly / isTrigger / isPositionTpsl)
        and skip anything protective, only sweeping plain resting entry limits.
        """
        now_ms = time.time() * 1000
        try:
            orders = self.info.frontend_open_orders(self.master_address)
        except Exception:  # noqa: BLE001 - fall back to basic listing
            orders = self.info.open_orders(self.master_address)
        for o in orders:
            if o.get("reduceOnly") or o.get("isTrigger") or o.get("isPositionTpsl"):
                continue  # protective order — leave it resting
            age_s = (now_ms - o.get("timestamp", now_ms)) / 1000
            if age_s >= ORDER_TIF_SECONDS:
                self._submit(
                    f"cancel stale {o['coin']} oid={o['oid']} (age {age_s:.0f}s)",
                    lambda o=o: self.exchange.cancel(o["coin"], o["oid"]),
                )

    def emergency_flatten(self) -> None:
        """Cancel all resting orders and market-close all positions (REST).
        Triggered when the WebSocket heartbeat is lost for >5s."""
        logger.warning("EMERGENCY FLATTEN triggered")
        for o in self.info.open_orders(self.master_address):
            self._submit(
                f"[emergency] cancel {o['coin']} oid={o['oid']}",
                lambda o=o: self.exchange.cancel(o["coin"], o["oid"]),
            )
        for coin in self.open_positions():
            self._submit(
                f"[emergency] market-close {coin}",
                lambda coin=coin: self.exchange.market_close(coin),
            )


def build_engine(dry_run: bool = True) -> ExecutionEngine:
    if not WALLET_PRIVATE_KEY or not MAIN_ACCOUNT_ADDRESS:
        raise RuntimeError("WALLET_PRIVATE_KEY and MAIN_ACCOUNT_ADDRESS must be set in .env")
    agent = eth_account.Account.from_key(WALLET_PRIVATE_KEY)
    exchange = Exchange(agent, API_URL, account_address=MAIN_ACCOUNT_ADDRESS)
    info = Info(API_URL, skip_ws=True)
    return ExecutionEngine(exchange, info, MAIN_ACCOUNT_ADDRESS, dry_run=dry_run)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    engine = build_engine(dry_run=True)
    state = engine.fetch_state()
    logger.info("Connected. Free margin: $%.2f", engine.available_margin(state))
    logger.info("Open positions: %s", engine.open_positions(state))
    logger.info("Orders used this hour: %d/%d", engine.rate_limiter.used, MAX_ORDERS_PER_HOUR)
