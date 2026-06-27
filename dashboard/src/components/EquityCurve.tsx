"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { PaperTrade } from "@/lib/types";
import { fmtUsd } from "@/lib/format";

interface Pt {
  t: number;
  equity: number;
  pnl: number;
  coin: string;
  n: number;
}

function parseTime(s: string | null): number | null {
  if (!s) return null;
  const ms = Date.parse(s);
  return Number.isNaN(ms) ? null : ms;
}

function fmtAxisTime(ms: number): string {
  const d = new Date(ms);
  return d.toLocaleString("en-US", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function CustomTooltip({ active, payload }: { active?: boolean; payload?: { payload: Pt }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-xs shadow-xl">
      <div className="mb-1 font-mono text-zinc-400">{fmtAxisTime(p.t)}</div>
      <div className="font-mono text-sm font-semibold text-zinc-100">{fmtUsd(p.equity)}</div>
      <div className="mt-1 flex items-center justify-between gap-4">
        <span className="text-zinc-500">{p.coin || "trade"} #{p.n}</span>
        <span className={p.pnl > 0 ? "text-emerald-400" : p.pnl < 0 ? "text-red-400" : "text-zinc-400"}>
          {p.pnl >= 0 ? "+" : ""}
          {fmtUsd(p.pnl)}
        </span>
      </div>
    </div>
  );
}

export function EquityCurve({ trades }: { trades: PaperTrade[] }) {
  const closed = trades
    .filter((t) => t.status === "closed" && t.equity_after !== null)
    .map((t, i) => {
      const t_ms = parseTime(t.exit_time) ?? parseTime(t.entry_time) ?? Date.now() + i;
      return {
        t: t_ms,
        equity: t.equity_after as number,
        pnl: t.realized_pnl ?? 0,
        coin: t.coin,
        n: i + 1,
      } as Pt;
    })
    .sort((a, b) => a.t - b.t);

  if (closed.length < 1) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-zinc-600">
        no closed trades yet , the equity curve appears once the journal has fills
      </div>
    );
  }

  // Baseline = equity before the first trade (equity_after − its pnl).
  const baseline = closed[0].equity - closed[0].pnl;
  const data: Pt[] =
    closed.length === 1
      ? [{ ...closed[0], t: closed[0].t - 60_000, equity: baseline, pnl: 0, n: 0 }, closed[0]]
      : closed;

  const last = data[data.length - 1].equity;
  const up = last >= baseline;
  const stroke = up ? "#34d399" : "#f87171";

  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={fmtAxisTime}
            tick={{ fill: "#71717a", fontSize: 10 }}
            stroke="#3f3f46"
            minTickGap={48}
          />
          <YAxis
            domain={["auto", "auto"]}
            tickFormatter={(v) => fmtUsd(v, 2)}
            tick={{ fill: "#71717a", fontSize: 10 }}
            stroke="#3f3f46"
            width={78}
            label={{
              value: "equity (usd)",
              angle: -90,
              position: "insideLeft",
              style: { fill: "#52525b", fontSize: 10, textAnchor: "middle" },
            }}
          />
          <Tooltip content={<CustomTooltip />} />
          <ReferenceLine
            y={baseline}
            stroke="#52525b"
            strokeDasharray="4 4"
            label={{ value: "start", fill: "#52525b", fontSize: 10, position: "insideBottomRight" }}
          />
          <Line
            type="monotone"
            dataKey="equity"
            stroke={stroke}
            strokeWidth={2}
            dot={{ r: 2, fill: stroke }}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
