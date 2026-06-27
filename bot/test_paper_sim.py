"""Deterministic synthetic tests for the paper-trading fill model.

These feed hand-crafted l2Book and trades messages straight into the engine
handlers (no network) and assert the queue-based maker fill, the depth-aware
taker-exit slippage, and the 15s TIF cancel all behave as specified.

Run:  ./.venv/bin/python test_paper_sim.py
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from execution import ORDER_NOTIONAL_USD, ORDER_TIF_SECONDS, STOP_LOSS_PCT
from journal import TradeJournal
from paper import MAKER_FEE, TAKER_FEE, PaperEngine, PaperPosition
from strategy import BUY_LONG

COIN = "TEST"


def _book_msg(coin: str, bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> dict:
    return {
        "channel": "l2Book",
        "data": {
            "coin": coin,
            "levels": [
                [{"px": str(px), "sz": str(sz)} for px, sz in bids],
                [{"px": str(px), "sz": str(sz)} for px, sz in asks],
            ],
        },
    }


def _trades_msg(coin: str, trades: list[tuple[str, float, float]]) -> dict:
    # trades: list of (side, px, sz). side "A"=aggressive sell, "B"=aggressive buy.
    return {
        "channel": "trades",
        "data": [{"coin": coin, "side": s, "px": str(px), "sz": str(sz)} for s, px, sz in trades],
    }


def _new_engine() -> tuple[PaperEngine, TradeJournal, Path]:
    db = Path(tempfile.mkdtemp()) / "test.db"
    journal = TradeJournal(db)
    engine = PaperEngine(info=None, coins=[COIN], journal=journal, equity=500.0)  # type: ignore[arg-type]
    return engine, journal, db


def test_queue_based_fill() -> None:
    engine, journal, _ = _new_engine()

    # Strongly bid-heavy book -> BUY_LONG signal. Best bid 100 has 5.0 resting.
    engine._handle_book(_book_msg(COIN, bids=[(100.0, 5.0), (99.0, 3.0)], asks=[(101.0, 1.0)]))

    order = engine._pending.get(COIN)
    assert order is not None, "expected a resting BUY order"
    assert order.side == BUY_LONG, f"expected BUY_LONG, got {order.side}"
    assert abs(order.queue_ahead - 5.0) < 1e-9, f"queue_ahead should be 5.0, got {order.queue_ahead}"
    assert COIN not in engine._positions, "must not be filled before any trades"
    print(f"PASS [queue] resting order placed @ {order.px} with queue_ahead={order.queue_ahead}")

    # Trades that should NOT count: wrong side (aggressive buy) and a sell above px.
    engine._handle_trades(_trades_msg(COIN, [("B", 100.0, 10.0), ("A", 100.5, 10.0)]))
    assert engine._pending[COIN].queue_ahead == 5.0, "non-qualifying trades must not burn the queue"
    assert COIN not in engine._positions, "must not fill on non-qualifying trades"
    print("PASS [queue] aggressive BUYS and sells above our px did not reduce the queue")

    # Qualifying aggressive sells at/below our px, cumulative < queue_ahead -> no fill.
    engine._handle_trades(_trades_msg(COIN, [("A", 100.0, 2.0), ("A", 99.5, 2.0)]))
    assert COIN not in engine._positions, "must not fill until cumulative volume exceeds queue_ahead"
    assert abs(engine._pending[COIN].queue_ahead - 1.0) < 1e-9
    print("PASS [queue] cumulative 4.0 < 5.0 queue -> still unfilled")

    # One more sell tips cumulative volume past the queue -> FILL at our px.
    engine._handle_trades(_trades_msg(COIN, [("A", 100.0, 2.0)]))
    assert COIN not in engine._pending, "order should be consumed on fill"
    pos = engine._positions.get(COIN)
    assert pos is not None, "expected a filled position after queue exhausted"
    assert abs(pos.entry_px - 100.0) < 1e-9, "maker fill must be at our resting px"
    expected_size = ORDER_NOTIONAL_USD / 100.0
    assert abs(pos.size - expected_size) < 1e-9
    print(f"PASS [queue] cumulative 6.0 > 5.0 queue -> FILLED @ {pos.entry_px} size={pos.size}")

    journal.close()


def test_taker_exit_slippage() -> None:
    engine, journal, _ = _new_engine()

    # Open a long directly (entry 100, size 0.1).
    entry_px, size = 100.0, ORDER_NOTIONAL_USD / 100.0
    sl_px = entry_px * (1 - STOP_LOSS_PCT)  # 98.5
    tp_px = entry_px * (1 + 0.015)
    tid = journal.record_entry(
        mode="paper", coin=COIN, side="long", signal=BUY_LONG,
        entry_px=entry_px, size=size, sl_px=sl_px, tp_px=tp_px,
    )
    engine._positions[COIN] = PaperPosition(tid, COIN, True, entry_px, size, sl_px, tp_px)

    # Book where the stop triggers (best_bid <= sl_px) but bid depth is thin, so
    # walking the book for our full size yields a price WORSE than the trigger.
    # bids: 0.05 @ 98.5 then 98.0; our size 0.1 sweeps both levels.
    engine._handle_book(_book_msg(
        COIN,
        bids=[(98.5, 0.05), (98.0, 1.0)],
        asks=[(98.6, 5.0), (98.7, 5.0)],
    ))

    closed = journal.closed_trades()
    assert len(closed) == 1, "stop-loss should have closed the position"
    exit_px = closed[0]["exit_px"]
    assert closed[0]["exit_reason"] == "stop_loss"
    # Expected = size-weighted avg of walking the bid book for our `size`,
    # derived from the actual size so it's robust to ORDER_NOTIONAL_USD changes.
    levels = [(98.5, 0.05), (98.0, 1.0)]
    remaining, cost = size, 0.0
    for px, qty in levels:
        take = min(remaining, qty)
        cost += take * px
        remaining -= take
        if remaining <= 0:
            break
    expected = cost / size
    assert abs(exit_px - expected) < 1e-9, f"exit_px {exit_px} != expected {expected}"
    slippage = sl_px - exit_px
    assert slippage > 0, f"taker exit must be worse than the {sl_px} trigger (slippage={slippage})"
    print(f"PASS [slippage] stop-loss trigger={sl_px} -> walked exit={exit_px:.4f} "
          f"(slippage={slippage:.4f} > 0)")

    journal.close()


def test_tif_cancel() -> None:
    engine, journal, _ = _new_engine()

    engine._handle_book(_book_msg(COIN, bids=[(100.0, 5.0)], asks=[(101.0, 1.0)]))
    assert COIN in engine._pending, "order should be resting"

    # Backdate placement beyond the TIF window.
    engine._pending[COIN].placed = time.time() - (ORDER_TIF_SECONDS + 1)

    # A neutral book tick (no fill, no new signal) should trigger the TIF cancel.
    engine._handle_book(_book_msg(COIN, bids=[(100.0, 1.0)], asks=[(101.0, 1.0)]))
    assert COIN not in engine._pending, "order should be cancelled after TIF expiry"
    assert COIN not in engine._positions, "expired order must not fill"
    print(f"PASS [tif] unfilled order cancelled after {ORDER_TIF_SECONDS}s TIF")

    journal.close()


def test_funding_accrual() -> None:
    engine, journal, _ = _new_engine()
    engine.funding_map = {COIN: 0.0001}  # positive hourly rate

    entry_px, size = 100.0, 0.1
    tid = journal.record_entry(mode="paper", coin=COIN, side="long", signal=BUY_LONG,
                               entry_px=entry_px, size=size)
    engine._positions[COIN] = PaperPosition(tid, COIN, True, entry_px, size, 98.5, 101.5)

    start_equity = engine.equity
    base = 1_000_000  # arbitrary epoch seconds anchor
    engine.accrue_funding(now=base)              # first observation: no charge
    assert engine.equity == start_equity
    engine.accrue_funding(now=base + 3600)       # next hour: long pays funding
    expected_debit = size * entry_px * 0.0001
    assert abs((start_equity - engine.equity) - expected_debit) < 1e-9, "long should pay funding"
    print(f"PASS [funding] long paid {expected_debit:.6f} across the funding hour "
          f"(equity {start_equity} -> {engine.equity:.6f})")

    journal.close()


if __name__ == "__main__":
    print("=== paper-sim deterministic tests ===")
    test_queue_based_fill()
    test_taker_exit_slippage()
    test_tif_cancel()
    test_funding_accrual()
    print("=== ALL TESTS PASSED ===")
