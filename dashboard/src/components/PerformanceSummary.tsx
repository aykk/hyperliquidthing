import type { PaperStats } from "@/lib/types";
import { fmtPct, fmtUsd } from "@/lib/format";

type Tone = "neutral" | "pos" | "neg" | "auto";

function toneClass(tone: Tone, value?: number): string {
  let t = tone;
  if (tone === "auto") {
    t = value === undefined || value === 0 ? "neutral" : value > 0 ? "pos" : "neg";
  }
  return t === "pos" ? "text-emerald-400" : t === "neg" ? "text-red-400" : "text-zinc-100";
}

function StatCard({
  label,
  value,
  tone = "neutral",
  raw,
}: {
  label: string;
  value: string;
  tone?: Tone;
  raw?: number;
}) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
      <div className="text-xs lowercase tracking-wider text-zinc-500">{label}</div>
      <div className={`mt-1 font-mono text-xl font-semibold ${toneClass(tone, raw)}`}>{value}</div>
    </div>
  );
}

function Breakdown({
  title,
  entries,
  fmt,
  color,
}: {
  title: string;
  entries: [string, number][];
  fmt: (v: number) => string;
  color?: boolean;
}) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
      <div className="mb-3 text-xs lowercase tracking-wider text-zinc-500">{title}</div>
      {entries.length === 0 ? (
        <p className="text-sm text-zinc-600">no data</p>
      ) : (
        <ul className="space-y-1.5">
          {entries.map(([k, v]) => (
            <li key={k} className="flex justify-between font-mono text-sm">
              <span className="text-zinc-400">{k}</span>
              <span
                className={
                  color
                    ? v > 0
                      ? "text-emerald-400"
                      : v < 0
                        ? "text-red-400"
                        : "text-zinc-300"
                    : "text-zinc-200"
                }
              >
                {fmt(v)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function PerformanceSummary({ stats }: { stats: PaperStats }) {
  const s = stats;
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <StatCard label="net pnl" value={fmtUsd(s.total_pnl ?? 0)} tone="auto" raw={s.total_pnl} />
        <StatCard label="win rate" value={fmtPct(s.win_rate_pct ?? 0)} />
        <StatCard label="expectancy" value={fmtUsd(s.expectancy ?? 0)} tone="auto" raw={s.expectancy} />
        <StatCard label="max drawdown" value={fmtUsd(s.max_drawdown ?? 0)} tone="neg" />
        <StatCard label="closed trades" value={String(s.closed_trades ?? 0)} />
        <StatCard label="fees paid" value={fmtUsd(s.total_fees ?? 0)} tone="neg" />
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
        <StatCard label="wins" value={String(s.wins ?? 0)} tone="pos" />
        <StatCard label="losses" value={String(s.losses ?? 0)} tone="neg" />
        <StatCard label="avg win" value={fmtUsd(s.avg_win ?? 0)} tone="pos" />
        <StatCard label="avg loss" value={fmtUsd(s.avg_loss ?? 0)} tone="neg" />
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Breakdown
          title="exits by reason"
          entries={Object.entries(s.by_exit_reason ?? {})}
          fmt={(v) => String(v)}
        />
        <Breakdown
          title="pnl by coin"
          entries={Object.entries(s.pnl_by_coin ?? {})}
          fmt={(v) => fmtUsd(v)}
          color
        />
        <Breakdown
          title="pnl by strategy"
          entries={Object.entries(s.pnl_by_strategy ?? {})}
          fmt={(v) => fmtUsd(v)}
          color
        />
      </div>
    </div>
  );
}
