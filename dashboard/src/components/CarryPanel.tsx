"use client";

import type { CarryStatus } from "@/lib/types";
import { fmtPct, fmtTime, fmtUsd } from "@/lib/format";

function uptime(startedAt: string | null): string {
  if (!startedAt) return "-";
  const start = Date.parse(startedAt);
  if (Number.isNaN(start)) return "-";
  let s = Math.max(0, Math.floor((Date.now() - start) / 1000));
  const d = Math.floor(s / 86400);
  s -= d * 86400;
  const h = Math.floor(s / 3600);
  s -= h * 3600;
  const m = Math.floor(s / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function pnlTone(v: number | null | undefined): string {
  if (v == null || v === 0) return "text-zinc-200";
  return v > 0 ? "text-emerald-400" : "text-red-400";
}

export function CarryPanel({
  carry,
  lastUpdate,
  liveTotalEquity,
}: {
  carry: CarryStatus | null;
  lastUpdate: number | null;
  liveTotalEquity?: number | null;
}) {
  if (!carry?.ready) {
    return (
      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h2 className="mb-2 text-sm font-semibold lowercase tracking-wider text-zinc-400">
          funding carry <span className="text-zinc-600">(carry_v1 · paper)</span>
        </h2>
        <p className="text-sm text-zinc-500">
          {carry?.reason ?? "carry allocator not running."}
        </p>
        <p className="mt-2 font-mono text-xs text-zinc-600">
          start: cd bot && IS_MAINNET=true ./.venv/bin/python carry.py paper
        </p>
      </section>
    );
  }

  const running = carry.running;
  const dot = running
    ? carry.stale
      ? "bg-red-500"
      : "bg-emerald-500"
    : "bg-zinc-600";
  const budget = carry.totalEquity ?? carry.capital;

  return (
    <section className="rounded-xl border border-emerald-900/40 bg-emerald-950/10 p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <span className={`h-2.5 w-2.5 rounded-full ${dot}`} />
          <h2 className="text-sm font-semibold lowercase tracking-wider text-zinc-200">
            funding carry <span className="text-zinc-500">({carry.strategy} · {carry.mode} · {carry.network})</span>
          </h2>
          <span className="font-mono text-xs lowercase text-zinc-500">{carry.state}</span>
        </div>
        {lastUpdate ? (
          <span className="font-mono text-[11px] text-zinc-600">updated {fmtTime(lastUpdate)}</span>
        ) : null}
      </div>

      <p className="mb-4 text-xs leading-relaxed text-zinc-500">
        paper simulation only: <span className="text-zinc-300">no real orders, your live wallet is not spent.</span>{" "}
        sizes against your wallet total equity ({fmtUsd(liveTotalEquity ?? budget)}), accrues funding hourly, rebalances every{" "}
        {carry.rebalanceMinutes ?? 60}m. live wallet below should stay flat while this runs.
      </p>

      {carry.message ? (
        <p className="mb-3 font-mono text-xs text-red-400">{carry.message}</p>
      ) : null}

      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
        <Stat label="sim total equity" value={fmtUsd(carry.equity ?? 0)} sub={`started ${fmtUsd(budget)}`} />
        <Stat
          label="sim pnl"
          value={`${(carry.pnl ?? 0) >= 0 ? "+" : ""}${fmtUsd(carry.pnl ?? 0)}`}
          tone={pnlTone(carry.pnl)}
        />
        <Stat
          label="sim deployed"
          value={`${fmtUsd(carry.deployed ?? 0)} (${carry.deployPct ?? 0}%)`}
        />
        <Stat label="live wallet" value={fmtUsd(liveTotalEquity ?? null)} sub="unchanged in paper" />
        <Stat label="blended apr" value={fmtPct(carry.blendedAprPct ?? 0, 1)} />
        <Stat label="net @ 30d" value={fmtPct(carry.netApr30dPct ?? 0, 1)} tone="text-emerald-400/90" />
      </div>

      <div className="mb-4 rounded-lg border border-zinc-800/80 bg-zinc-900/40 p-4">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs lowercase text-zinc-500">
          <span>break-even progress (sim funding vs sim entry cost)</span>
          <span className="font-mono text-zinc-400">
            {fmtPct(carry.breakevenPct ?? 0, 0)} · uptime {uptime(carry.startedAt)}
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-zinc-800">
          <div
            className="h-full rounded-full bg-emerald-500/80 transition-all duration-500"
            style={{ width: `${Math.min(100, Math.max(0, carry.breakevenPct ?? 0))}%` }}
          />
        </div>
        <div className="mt-2 flex flex-wrap justify-between gap-2 font-mono text-[11px] text-zinc-600">
          <span>funding earned: {fmtUsd(carry.fundingEarned ?? 0)}</span>
          <span>entry cost paid: {fmtUsd(carry.entryCostPaid ?? 0)}</span>
          <span>est. break-even ~7-8 days at current rates</span>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-800 text-xs lowercase tracking-wider text-zinc-500">
              <th className="pb-2 pr-4 font-medium">coin</th>
              <th className="pb-2 pr-4 font-medium">perp</th>
              <th className="pb-2 pr-4 font-medium text-right">notional</th>
              <th className="pb-2 pr-4 font-medium text-right">accrued</th>
              <th className="pb-2 pr-4 font-medium text-right">funding apr</th>
              <th className="pb-2 font-medium text-right">b/e days</th>
            </tr>
          </thead>
          <tbody>
            {carry.positions.length === 0 ? (
              <tr>
                <td colSpan={6} className="py-4 text-center text-zinc-600">
                  no open carries, waiting for rebalance
                </td>
              </tr>
            ) : (
              carry.positions.map((p) => (
                <tr key={p.coin} className="border-b border-zinc-800/50 font-mono text-zinc-300">
                  <td className="py-2 pr-4">{p.coin}</td>
                  <td className="py-2 pr-4 lowercase">{p.side}</td>
                  <td className="py-2 pr-4 text-right">{fmtUsd(p.notional)}</td>
                  <td className={`py-2 pr-4 text-right ${pnlTone(p.accrued)}`}>
                    {p.accrued >= 0 ? "+" : ""}
                    {fmtUsd(p.accrued)}
                  </td>
                  <td className="py-2 pr-4 text-right">{fmtPct(p.funding_apr_pct, 1)}</td>
                  <td className="py-2 text-right">{p.breakeven_days?.toFixed(1) ?? "-"}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Stat({
  label,
  value,
  sub,
  tone = "text-zinc-100",
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: string;
}) {
  return (
    <div>
      <div className="text-[10px] lowercase tracking-wider text-zinc-500">{label}</div>
      <div className={`mt-0.5 font-mono text-sm font-semibold ${tone}`}>{value}</div>
      {sub ? <div className="mt-0.5 font-mono text-[10px] text-zinc-600">{sub}</div> : null}
    </div>
  );
}
