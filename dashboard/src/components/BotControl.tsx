"use client";

import { useEffect, useState } from "react";
import { useBotControl } from "@/lib/useBotControl";
import { fmtUsd } from "@/lib/format";

function uptime(startedAt: string | null): string {
  if (!startedAt) return ",";
  const start = Date.parse(startedAt);
  if (Number.isNaN(start)) return ",";
  let s = Math.max(0, Math.floor((Date.now() - start) / 1000));
  const h = Math.floor(s / 3600);
  s -= h * 3600;
  const m = Math.floor(s / 60);
  s -= m * 60;
  return h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${s}s` : `${s}s`;
}

export function BotControl() {
  const { status, error, busy, start, stop } = useBotControl();
  // Tick so the uptime label stays live between polls.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const state = status?.state ?? "stopped";
  const running = Boolean(status?.running);
  const transitioning = state === "starting" || state === "stopping";

  const dot =
    state === "running"
      ? "bg-emerald-500"
      : transitioning
        ? "bg-amber-500 animate-pulse"
        : status?.stale
          ? "bg-red-500"
          : "bg-zinc-600";

  return (
    <section className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <div className="flex items-center gap-2">
          <span className={`h-2.5 w-2.5 rounded-full ${dot}`} />
          <span className="text-sm font-semibold lowercase text-zinc-200">
            {status?.network ? `${status.network} bot` : "bot"}
          </span>
          <span className="font-mono text-xs lowercase text-zinc-500">{state}</span>
          <span className="font-mono text-[10px] text-zinc-600">(obi · live testnet)</span>
        </div>
        {running && (
          <>
            <Meta label="mode" value={`${status?.mode ?? "live"}${status?.network ? ` · ${status.network}` : ""}`} />
            <Meta label="strategy" value={status?.strategy ?? ","} />
            <Meta label="equity" value={status?.equity != null ? fmtUsd(status.equity) : ","} />
            <Meta label="open" value={String(status?.openPositions ?? 0)} />
            <Meta label="uptime" value={uptime(status?.startedAt ?? null)} />
            {status?.coins?.length ? <Meta label="coins" value={status.coins.join(" ")} /> : null}
          </>
        )}
        {status?.stale && (
          <span className="font-mono text-xs text-red-400">{status.message ?? "process gone"}</span>
        )}
      </div>

      <div className="flex items-center gap-3">
        {error && <span className="font-mono text-xs text-red-400">{error}</span>}
        {running ? (
          <button
            type="button"
            disabled={busy || state === "stopping"}
            onClick={stop}
            className="rounded-md border border-red-800/60 bg-red-950/40 px-3.5 py-1.5 text-sm font-medium lowercase text-red-300 hover:bg-red-900/40 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {state === "stopping" ? "stopping…" : "stop bot"}
          </button>
        ) : (
          <button
            type="button"
            disabled={busy || state === "starting"}
            onClick={start}
            className="rounded-md border border-emerald-800/60 bg-emerald-950/40 px-3.5 py-1.5 text-sm font-medium lowercase text-emerald-300 hover:bg-emerald-900/40 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {state === "starting" ? "starting…" : "start bot"}
          </button>
        )}
      </div>
    </section>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] lowercase tracking-wider text-zinc-500">{label}</span>
      <span className="font-mono text-sm text-zinc-200">{value}</span>
    </div>
  );
}
