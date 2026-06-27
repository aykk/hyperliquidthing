import path from "node:path";
import fs from "node:fs";
import Database from "better-sqlite3";
import { NextResponse } from "next/server";

// better-sqlite3 is a native module; force the Node runtime (not edge) and never cache.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// The Python bot writes the journal to <repo>/bot/data/trades.db. The dashboard runs
// from <repo>/dashboard, so the default path is one level up. Overridable for deploys.
function dbPath(): string {
  return (
    process.env.PAPER_DB_PATH ||
    path.join(process.cwd(), "..", "bot", "data", "trades.db")
  );
}

interface TradeRow {
  id: number;
  mode: string;
  strategy: string;
  coin: string;
  side: string;
  signal: string;
  status: string;
  entry_time: string;
  entry_px: number;
  size: number;
  notional: number;
  bid_ratio_entry: number | null;
  spread_pct_entry: number | null;
  funding_entry: number | null;
  sl_px: number | null;
  tp_px: number | null;
  exit_time: string | null;
  exit_px: number | null;
  exit_reason: string | null;
  fees: number | null;
  realized_pnl: number | null;
  return_pct: number | null;
  equity_after: number | null;
}

function mean(xs: number[]): number {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0;
}

function round(n: number, dp = 4): number {
  const f = 10 ** dp;
  return Math.round(n * f) / f;
}

function computeStats(closed: TradeRow[]) {
  if (closed.length === 0) return { closed_trades: 0 };
  const pnls = closed.map((t) => t.realized_pnl ?? 0);
  const wins = pnls.filter((p) => p > 0);
  const losses = pnls.filter((p) => p <= 0);

  const equity = closed
    .map((t) => t.equity_after)
    .filter((v): v is number => v !== null && v !== undefined);
  let peak = -Infinity;
  let maxDd = 0;
  for (const v of equity) {
    peak = Math.max(peak, v);
    maxDd = Math.min(maxDd, v - peak);
  }

  const byReason: Record<string, number> = {};
  const byCoin: Record<string, number> = {};
  const byStrategy: Record<string, number> = {};
  for (const t of closed) {
    const r = t.exit_reason ?? "unknown";
    byReason[r] = (byReason[r] ?? 0) + 1;
    byCoin[t.coin] = (byCoin[t.coin] ?? 0) + (t.realized_pnl ?? 0);
    const s = t.strategy ?? "default";
    byStrategy[s] = (byStrategy[s] ?? 0) + (t.realized_pnl ?? 0);
  }

  return {
    closed_trades: closed.length,
    wins: wins.length,
    losses: losses.length,
    win_rate_pct: round((wins.length / closed.length) * 100, 2),
    total_pnl: round(pnls.reduce((a, b) => a + b, 0)),
    total_fees: round(closed.reduce((a, t) => a + (t.fees ?? 0), 0)),
    avg_pnl: round(mean(pnls)),
    avg_win: wins.length ? round(mean(wins)) : 0,
    avg_loss: losses.length ? round(mean(losses)) : 0,
    expectancy: round(mean(pnls)),
    max_drawdown: round(Math.abs(maxDd)),
    by_exit_reason: byReason,
    pnl_by_coin: Object.fromEntries(
      Object.entries(byCoin).map(([k, v]) => [k, round(v)]),
    ),
    pnl_by_strategy: Object.fromEntries(
      Object.entries(byStrategy).map(([k, v]) => [k, round(v)]),
    ),
  };
}

export async function GET() {
  const file = dbPath();
  if (!fs.existsSync(file)) {
    return NextResponse.json({
      ready: false,
      reason: "no journal yet , run the paper engine to generate trades",
      stats: { closed_trades: 0 },
      trades: [],
    });
  }

  let db: Database.Database | null = null;
  try {
    db = new Database(file, { readonly: true, fileMustExist: true });
    db.pragma("busy_timeout = 3000");
    const hasTable = db
      .prepare(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trades'",
      )
      .get();
    if (!hasTable) {
      return NextResponse.json({
        ready: false,
        reason: "journal exists but has no trades table yet",
        stats: { closed_trades: 0 },
        trades: [],
      });
    }
    const trades = db
      .prepare("SELECT * FROM trades ORDER BY id")
      .all() as TradeRow[];
    const closed = trades.filter((t) => t.status === "closed");
    const open = trades.filter((t) => t.status === "open");
    return NextResponse.json({
      ready: true,
      stats: computeStats(closed),
      openCount: open.length,
      trades,
    });
  } catch (err) {
    return NextResponse.json(
      { ready: false, reason: String(err), stats: { closed_trades: 0 }, trades: [] },
      { status: 500 },
    );
  } finally {
    db?.close();
  }
}
