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
import type { EquityPoint } from "@/lib/useEquityHistory";
import { fmtUsd } from "@/lib/format";

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

function CustomTooltip({ active, payload }: { active?: boolean; payload?: { payload: EquityPoint }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-xs shadow-xl">
      <div className="mb-1 font-mono text-zinc-400">{fmtAxisTime(p.t)}</div>
      <div className="font-mono text-sm font-semibold text-zinc-100">{fmtUsd(p.equity)}</div>
    </div>
  );
}

export function LiveEquityCurve({
  points,
  currentEquity,
}: {
  points: EquityPoint[];
  currentEquity: number | null | undefined;
}) {
  const data =
    points.length >= 1
      ? points
      : currentEquity != null && currentEquity > 0
        ? [{ t: Date.now(), equity: currentEquity }]
        : [];

  if (data.length < 1) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-zinc-600">
        waiting for live account data from hyperliquid…
      </div>
    );
  }

  const baseline = data[0].equity;
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
            tickFormatter={(v) => fmtUsd(v, 0)}
            tick={{ fill: "#71717a", fontSize: 10 }}
            stroke="#3f3f46"
            width={72}
            label={{
              value: "total equity (usd)",
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
            dot={data.length <= 24 ? { r: 2, fill: stroke } : false}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
