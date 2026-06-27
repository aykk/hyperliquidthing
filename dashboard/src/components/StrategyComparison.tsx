"use client";

// Per-strategy A/B comparison built from the trade journal. The point is a
// one-glance read on which variant (obi_v1/v2/v3, paper vs live) actually has
// an edge once fees are accounted for.
//
// IMPORTANT normalization: the journal stores realized_pnl differently by mode,
// the paper engine records it NET (fees already subtracted) while the live engine
// records the venue's GROSS closedPnl with fees in a separate column. We reconcile
// both to a consistent net/gross here so the rows are comparable.

import { useMemo } from "react";
import type { PaperTrade } from "@/lib/types";
import { fmtUsd } from "@/lib/format";

interface Row {
  key: string;
  strategy: string;
  mode: string;
  n: number;
  wins: number;
  gross: number;
  fees: number;
  net: number;
}

function reconcile(t: PaperTrade): { net: number; gross: number; fee: number } {
  const pnl = t.realized_pnl ?? 0;
  const fee = t.fees ?? 0;
  if (t.mode === "paper") return { net: pnl, gross: pnl + fee, fee };
  return { net: pnl - fee, gross: pnl, fee }; // live stores gross
}

function pnlClass(n: number): string {
  if (n === 0) return "text-zinc-400";
  return n > 0 ? "text-emerald-400" : "text-red-400";
}

export function StrategyComparison({ trades }: { trades: PaperTrade[] }) {
  const rows = useMemo(() => {
    const m = new Map<string, Row>();
    for (const t of trades) {
      if (t.status !== "closed") continue;
      const strategy = t.strategy ?? "default";
      const key = `${strategy} · ${t.mode}`;
      const r =
        m.get(key) ??
        { key, strategy, mode: t.mode, n: 0, wins: 0, gross: 0, fees: 0, net: 0 };
      const { net, gross, fee } = reconcile(t);
      r.n += 1;
      r.wins += net > 0 ? 1 : 0;
      r.gross += gross;
      r.fees += fee;
      r.net += net;
      m.set(key, r);
    }
    return Array.from(m.values()).sort((a, b) => b.net - a.net);
  }, [trades]);

  if (rows.length === 0) return null;

  const cell = "whitespace-nowrap px-3 py-2 text-right";

  return (
    <section className="flex flex-col gap-2 rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold lowercase tracking-wider text-zinc-400">
          strategy comparison (closed trades)
        </h2>
        <span className="font-mono text-[10px] text-zinc-600">
          net = after fees · win% = net-positive trades
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800 text-xs lowercase tracking-wider text-zinc-500">
              <th className="px-3 py-2 text-left font-medium">strategy · mode</th>
              <th className="px-3 py-2 text-right font-medium">trades</th>
              <th className="px-3 py-2 text-right font-medium">win%</th>
              <th className="px-3 py-2 text-right font-medium">gross</th>
              <th className="px-3 py-2 text-right font-medium">fees</th>
              <th className="px-3 py-2 text-right font-medium">net</th>
              <th className="px-3 py-2 text-right font-medium">avg net / trade</th>
            </tr>
          </thead>
          <tbody className="font-mono">
            {rows.map((r) => {
              const avg = r.n ? r.net / r.n : 0;
              const winPct = r.n ? (r.wins / r.n) * 100 : 0;
              return (
                <tr key={r.key} className="border-b border-zinc-800/50 last:border-0 hover:bg-zinc-800/30">
                  <td className="whitespace-nowrap px-3 py-2 text-left">
                    <span className="font-sans font-semibold text-zinc-200">{r.strategy}</span>
                    <span className="ml-2 text-zinc-500">{r.mode}</span>
                  </td>
                  <td className={`${cell} text-zinc-300`}>{r.n}</td>
                  <td className={`${cell} text-zinc-300`}>{winPct.toFixed(0)}%</td>
                  <td className={`${cell} ${pnlClass(r.gross)}`}>{fmtUsd(r.gross)}</td>
                  <td className={`${cell} text-zinc-500`}>{fmtUsd(r.fees)}</td>
                  <td className={`${cell} font-semibold ${pnlClass(r.net)}`}>{fmtUsd(r.net)}</td>
                  <td className={`${cell} ${pnlClass(avg)}`}>{fmtUsd(avg)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] leading-relaxed text-zinc-600">
        positive net expectancy (avg net / trade &gt; 0) over a large sample is the bar
        for a real edge. tiny samples are not conclusive.
      </p>
    </section>
  );
}
