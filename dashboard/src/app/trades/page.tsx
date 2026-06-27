"use client";

import { useMemo, useState } from "react";
import { TradesTable } from "@/components/TradesTable";
import { StrategyComparison } from "@/components/StrategyComparison";
import { EquityCurve } from "@/components/EquityCurve";
import { PerformanceSummary } from "@/components/PerformanceSummary";
import { StrategySelect } from "@/components/StrategySelect";
import { usePaperData } from "@/lib/usePaperData";
import { computeStats, strategiesWithClosed } from "@/lib/stats";
import { fmtTime } from "@/lib/format";

export default function TradesPage() {
  const { data, lastUpdate, loading } = usePaperData(true);
  const trades = data?.trades ?? [];
  const strategies = useMemo(() => strategiesWithClosed(trades), [trades]);
  const [strat, setStrat] = useState<string>("carry_v1");
  const filteredTrades = useMemo(() => {
    let rows = strat === "all" ? trades : trades.filter((t) => t.strategy === strat);
    // Old carry runs used STARTING_EQUITY=$240; exclude them from carry_v1 analytics.
    if (strat === "carry_v1") {
      rows = rows.filter(
        (t) =>
          t.status === "open" ||
          t.equity_after == null ||
          t.equity_after >= 500,
      );
    }
    return rows;
  }, [trades, strat]);
  const stats = useMemo(() => computeStats(filteredTrades), [filteredTrades]);
  const journalReady = (stats.closed_trades ?? 0) > 0;

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-5 p-4 sm:p-6">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 pb-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-zinc-100">trades</h1>
          <p className="font-mono text-xs text-zinc-500">
            {trades.length} trades in journal
            {lastUpdate ? <span className="ml-2 text-zinc-600">· updated {fmtTime(lastUpdate)}</span> : null}
          </p>
        </div>
      </header>

      {trades.length === 0 ? (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-8 text-center">
          <p className="text-sm text-zinc-400">{loading ? "loading journal…" : "no trades yet."}</p>
          <p className="mt-2 text-xs text-zinc-600">
            {data?.reason ??
              "run the paper engine (python -m bot.paper, or ./.venv/bin/python paper.py) to log trades."}
          </p>
        </div>
      ) : (
        <>
          <StrategyComparison trades={trades} />

          <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-sm font-semibold lowercase tracking-wider text-zinc-400">
                strategy performance <span className="text-zinc-600">(paper journal)</span>
              </h2>
              <div className="flex flex-wrap items-center gap-3">
                {strategies.length > 0 && (
                  <StrategySelect strategies={strategies} value={strat} onChange={setStrat} />
                )}
                <span className="font-mono text-[11px] text-zinc-600">
                  {journalReady ? `${stats.closed_trades} closed trades` : "awaiting closed trades"}
                </span>
              </div>
            </div>
            <p className="mb-3 text-xs text-zinc-600">
              simulated equity for the selected strategy (not your live wallet). live total equity is on the portfolio page.
            </p>
            <EquityCurve trades={filteredTrades} />
          </section>

          {journalReady && <PerformanceSummary stats={stats} />}

          <TradesTable trades={trades} />
        </>
      )}
    </main>
  );
}
