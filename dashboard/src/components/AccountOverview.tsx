import type { AccountSummary } from "@/lib/types";
import { fmtPct, fmtUsd } from "@/lib/format";

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs lowercase tracking-wider text-zinc-500">{label}</span>
      <span className="font-mono text-2xl font-semibold text-zinc-100">{value}</span>
      {sub && <span className="text-xs text-zinc-500">{sub}</span>}
    </div>
  );
}

export function AccountOverview({ account }: { account: AccountSummary | null }) {
  const pct = account?.marginUsedPct ?? 0;
  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
      <h2 className="mb-4 text-sm font-semibold lowercase tracking-wider text-zinc-400">
        account overview
      </h2>
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-3">
        <Stat
          label="total equity"
          value={fmtUsd(account?.totalEquity)}
          sub={
            account
              ? `perps ${fmtUsd(account.perpsAccountValue)} · spot ${fmtUsd(account.spotUsdc)}`
              : undefined
          }
        />
        <Stat label="available margin" value={fmtUsd(account?.availableMargin)} />
        <div className="flex flex-col gap-2">
          <span className="text-xs lowercase tracking-wider text-zinc-500">margin usage</span>
          <span className="font-mono text-2xl font-semibold text-zinc-100">{fmtPct(pct)}</span>
          <div className="h-2 w-full overflow-hidden rounded-full bg-zinc-800">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                pct > 80 ? "bg-red-500" : pct > 50 ? "bg-amber-500" : "bg-emerald-500"
              }`}
              style={{ width: `${Math.min(100, Math.max(2, pct))}%` }}
            />
          </div>
          <span className="text-xs text-zinc-500">{fmtUsd(account?.marginUsed)} locked</span>
        </div>
      </div>
    </section>
  );
}
