import type { PaperStats, PaperTrade } from "@/lib/types";

/**
 * Recompute PaperStats from an arbitrary subset of trades, client-side.
 * Mirrors bot/journal.py `stats()` so a strategy-filtered view shows the same
 * numbers the backend would produce for that slice.
 */
export function computeStats(trades: PaperTrade[]): PaperStats {
  const closed = trades.filter((t) => t.status === "closed");
  if (closed.length === 0) return { closed_trades: 0 };

  const pnls = closed.map((t) => t.realized_pnl ?? 0);
  const wins = pnls.filter((p) => p > 0);
  const losses = pnls.filter((p) => p <= 0);
  const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);

  // Max drawdown over the equity_after sequence, ordered by exit time.
  const equitySeq = closed
    .filter((t) => t.equity_after !== null)
    .sort((a, b) => Date.parse(a.exit_time ?? "") - Date.parse(b.exit_time ?? ""))
    .map((t) => t.equity_after as number);
  let peak = -Infinity;
  let maxDd = 0;
  for (const v of equitySeq) {
    peak = Math.max(peak, v);
    maxDd = Math.min(maxDd, v - peak);
  }

  const byReason: Record<string, number> = {};
  const byCoin: Record<string, number> = {};
  const byStrategy: Record<string, number> = {};
  for (const t of closed) {
    const reason = t.exit_reason ?? "?";
    byReason[reason] = (byReason[reason] ?? 0) + 1;
    byCoin[t.coin] = (byCoin[t.coin] ?? 0) + (t.realized_pnl ?? 0);
    byStrategy[t.strategy] = (byStrategy[t.strategy] ?? 0) + (t.realized_pnl ?? 0);
  }

  const round = (n: number, d = 4) => Number(n.toFixed(d));
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
    pnl_by_coin: Object.fromEntries(Object.entries(byCoin).map(([k, v]) => [k, round(v)])),
    pnl_by_strategy: Object.fromEntries(Object.entries(byStrategy).map(([k, v]) => [k, round(v)])),
  };
}

/** Distinct strategies with at least one closed or open trade, ordered. */
export function strategiesWithClosed(trades: PaperTrade[]): string[] {
  const set = new Set<string>();
  for (const t of trades) {
    if (t.status === "closed" || t.status === "open") set.add(t.strategy);
  }
  return [...set].sort();
}
