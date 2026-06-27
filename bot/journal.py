"""Trade Journal (SQLite).

Single source of truth for every trade the bot makes, in paper or live mode.
Each row captures not just the outcome but the DECISION CONTEXT (features at entry:
bid_ratio, spread, funding) so the data can be exported and analysed to correlate
conditions with profitability.

Used by the paper engine now; the live engine and dashboard read from the same DB.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import statistics
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "trades.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mode            TEXT NOT NULL,           -- 'paper' | 'live'
    strategy        TEXT NOT NULL DEFAULT 'default',  -- strategy/version label
    coin            TEXT NOT NULL,
    side            TEXT NOT NULL,           -- 'long' | 'short'
    signal          TEXT NOT NULL,
    status          TEXT NOT NULL,           -- 'open' | 'closed'
    -- entry
    entry_time      TEXT NOT NULL,
    entry_px        REAL NOT NULL,
    size            REAL NOT NULL,
    notional        REAL NOT NULL,
    -- decision-context features (logged at entry)
    bid_ratio_entry REAL,
    spread_pct_entry REAL,
    funding_entry   REAL,
    -- protective levels
    sl_px           REAL,
    tp_px           REAL,
    -- exit
    exit_time       TEXT,
    exit_px         REAL,
    exit_reason     TEXT,                    -- 'take_profit' | 'stop_loss' | 'signal_flip' | 'manual'
    -- accounting
    fees            REAL DEFAULT 0,
    realized_pnl    REAL,
    return_pct      REAL,
    equity_after    REAL
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TradeJournal:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the WebSocket callback thread writes while the
        # main thread reads stats. All access is serialized by self._lock.
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            # WAL lets the dashboard read the journal concurrently while the bot
            # writes, without "database is locked" errors.
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(_SCHEMA)
            self._migrate()
            self.conn.commit()

    def _migrate(self) -> None:
        """Add any columns missing from an older DB (forward-compatible)."""
        existing = {row["name"] for row in self.conn.execute("PRAGMA table_info(trades)")}
        if "strategy" not in existing:
            self.conn.execute(
                "ALTER TABLE trades ADD COLUMN strategy TEXT NOT NULL DEFAULT 'default'"
            )

    # --- writes ----------------------------------------------------------------
    def record_entry(
        self,
        *,
        mode: str,
        coin: str,
        side: str,
        signal: str,
        entry_px: float,
        size: float,
        strategy: str = "default",
        bid_ratio: float | None = None,
        spread_pct: float | None = None,
        funding: float | None = None,
        sl_px: float | None = None,
        tp_px: float | None = None,
        entry_time: str | None = None,
    ) -> int:
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO trades (mode, strategy, coin, side, signal, status, entry_time,
                    entry_px, size, notional, bid_ratio_entry, spread_pct_entry, funding_entry,
                    sl_px, tp_px)
                   VALUES (?,?,?,?,?,'open',?,?,?,?,?,?,?,?,?)""",
                (mode, strategy, coin, side, signal, entry_time or _utc_now(), entry_px, size,
                 size * entry_px, bid_ratio, spread_pct, funding, sl_px, tp_px),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def record_exit(
        self,
        trade_id: int,
        *,
        exit_px: float,
        exit_reason: str,
        fees: float,
        realized_pnl: float,
        equity_after: float,
        exit_time: str | None = None,
    ) -> None:
        with self._lock:
            row = self.conn.execute("SELECT notional FROM trades WHERE id=?", (trade_id,)).fetchone()
            notional = row["notional"] if row else 0
            return_pct = (realized_pnl / notional * 100.0) if notional else 0.0
            self.conn.execute(
                """UPDATE trades SET status='closed', exit_time=?, exit_px=?, exit_reason=?,
                    fees=?, realized_pnl=?, return_pct=?, equity_after=? WHERE id=?""",
                (exit_time or _utc_now(), exit_px, exit_reason, fees, realized_pnl,
                 return_pct, equity_after, trade_id),
            )
            self.conn.commit()

    # --- reads -----------------------------------------------------------------
    def _rows(self, where: str = "") -> list[dict[str, Any]]:
        q = "SELECT * FROM trades"
        if where:
            q += f" WHERE {where}"
        q += " ORDER BY id"
        with self._lock:
            return [dict(r) for r in self.conn.execute(q).fetchall()]

    def all_trades(self) -> list[dict[str, Any]]:
        return self._rows()

    def open_trades(self) -> list[dict[str, Any]]:
        return self._rows("status='open'")

    def closed_trades(self) -> list[dict[str, Any]]:
        return self._rows("status='closed'")

    # --- analytics -------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        closed = self.closed_trades()
        if not closed:
            return {"closed_trades": 0}
        pnls = [t["realized_pnl"] or 0 for t in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        equity_curve = [t["equity_after"] for t in closed if t["equity_after"] is not None]
        max_dd = self._max_drawdown(equity_curve)

        by_reason: dict[str, int] = {}
        by_coin_pnl: dict[str, float] = {}
        by_strategy_pnl: dict[str, float] = {}
        for t in closed:
            by_reason[t["exit_reason"]] = by_reason.get(t["exit_reason"], 0) + 1
            by_coin_pnl[t["coin"]] = by_coin_pnl.get(t["coin"], 0.0) + (t["realized_pnl"] or 0)
            strat = t["strategy"] if "strategy" in t.keys() else "default"
            by_strategy_pnl[strat] = by_strategy_pnl.get(strat, 0.0) + (t["realized_pnl"] or 0)

        return {
            "closed_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(len(wins) / len(closed) * 100, 2),
            "total_pnl": round(sum(pnls), 4),
            "total_fees": round(sum(t["fees"] or 0 for t in closed), 4),
            "avg_pnl": round(statistics.mean(pnls), 4),
            "avg_win": round(statistics.mean(wins), 4) if wins else 0,
            "avg_loss": round(statistics.mean(losses), 4) if losses else 0,
            "expectancy": round(statistics.mean(pnls), 4),
            "max_drawdown": round(max_dd, 4),
            "by_exit_reason": by_reason,
            "pnl_by_coin": {k: round(v, 4) for k, v in by_coin_pnl.items()},
            "pnl_by_strategy": {k: round(v, 4) for k, v in by_strategy_pnl.items()},
        }

    @staticmethod
    def _max_drawdown(equity: list[float]) -> float:
        peak = float("-inf")
        max_dd = 0.0
        for v in equity:
            peak = max(peak, v)
            max_dd = min(max_dd, v - peak)
        return abs(max_dd)

    # --- export ----------------------------------------------------------------
    def export_csv(self, path: Path | str) -> Path:
        rows = self.all_trades()
        path = Path(path)
        with path.open("w", newline="") as f:
            if rows:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
        return path

    def export_json(self, path: Path | str) -> Path:
        path = Path(path)
        payload = {"stats": self.stats(), "trades": self.all_trades()}
        path.write_text(json.dumps(payload, indent=2))
        return path

    def close(self) -> None:
        self.conn.close()
