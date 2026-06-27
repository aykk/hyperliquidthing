"use client";

import { useMemo, useState } from "react";
import type { PaperTrade } from "@/lib/types";
import { fmtDateTime, fmtNum, fmtPct, fmtPx, fmtUsd } from "@/lib/format";

type SortKey =
  | "entry_time"
  | "strategy"
  | "coin"
  | "side"
  | "status"
  | "entry_px"
  | "exit_px"
  | "exit_reason"
  | "notional"
  | "return_pct"
  | "realized_pnl"
  | "fees"
  | "bid_ratio_entry"
  | "spread_pct_entry"
  | "funding_entry";

interface Col {
  key: SortKey;
  label: string;
  align?: "right";
  render: (t: PaperTrade) => React.ReactNode;
}

const PAGE_SIZES = [25, 50, 100, 250];

function pnlClass(n: number | null | undefined): string {
  if (n === null || n === undefined || n === 0) return "text-zinc-400";
  return n > 0 ? "text-emerald-400" : "text-red-400";
}

function uniq<T>(xs: T[]): T[] {
  return Array.from(new Set(xs));
}

function cmp(a: unknown, b: unknown): number {
  const an = a === null || a === undefined;
  const bn = b === null || b === undefined;
  if (an && bn) return 0;
  if (an) return 1; // nulls last
  if (bn) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b));
}

function toCsv(rows: PaperTrade[]): string {
  if (rows.length === 0) return "";
  const cols = Object.keys(rows[0]);
  const esc = (v: unknown) => {
    if (v === null || v === undefined) return "";
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [cols.join(","), ...rows.map((r) =>
    cols.map((c) => esc((r as unknown as Record<string, unknown>)[c])).join(","),
  )].join("\n");
}

function download(filename: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function TradesTable({ trades }: { trades: PaperTrade[] }) {
  const [mode, setMode] = useState("all");
  const [strategy, setStrategy] = useState("all");
  const [status, setStatus] = useState("all");
  const [side, setSide] = useState("all");
  const [coin, setCoin] = useState("all");
  const [reason, setReason] = useState("all");
  const [sort, setSort] = useState<{ key: SortKey; dir: "asc" | "desc" }>({
    key: "entry_time",
    dir: "desc",
  });
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);

  const coins = useMemo(() => uniq(trades.map((t) => t.coin)).sort(), [trades]);
  const reasons = useMemo(
    () => uniq(trades.map((t) => t.exit_reason).filter(Boolean) as string[]).sort(),
    [trades],
  );
  const modes = useMemo(() => uniq(trades.map((t) => t.mode)).sort(), [trades]);
  const strategies = useMemo(
    () => uniq(trades.map((t) => t.strategy ?? "default")).sort(),
    [trades],
  );

  const filtered = useMemo(() => {
    const out = trades.filter(
      (t) =>
        (mode === "all" || t.mode === mode) &&
        (strategy === "all" || (t.strategy ?? "default") === strategy) &&
        (status === "all" || t.status === status) &&
        (side === "all" || t.side === side) &&
        (coin === "all" || t.coin === coin) &&
        (reason === "all" || t.exit_reason === reason),
    );
    out.sort((a, b) => {
      const r = cmp(a[sort.key], b[sort.key]);
      return sort.dir === "asc" ? r : -r;
    });
    return out;
  }, [trades, mode, strategy, status, side, coin, reason, sort]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, pageCount - 1);
  const pageRows = filtered.slice(safePage * pageSize, safePage * pageSize + pageSize);

  const cols: Col[] = [
    { key: "entry_time", label: "entry time", render: (t) => <span className="text-zinc-500">{fmtDateTime(t.entry_time)}</span> },
    { key: "strategy", label: "strategy", render: (t) => <span className="text-zinc-400">{t.strategy ?? "default"}</span> },
    { key: "coin", label: "coin", render: (t) => <span className="font-sans font-semibold text-zinc-100">{t.coin}</span> },
    {
      key: "side",
      label: "side",
      render: (t) => <span className={t.side === "long" ? "text-emerald-400" : "text-red-400"}>{t.side}</span>,
    },
    {
      key: "status",
      label: "status",
      render: (t) => (
        <span className={t.status === "open" ? "text-amber-400" : "text-zinc-400"}>{t.status}</span>
      ),
    },
    { key: "entry_px", label: "entry", align: "right", render: (t) => <span className="text-zinc-300">{fmtPx(t.entry_px)}</span> },
    { key: "exit_px", label: "exit", align: "right", render: (t) => <span className="text-zinc-300">{fmtPx(t.exit_px)}</span> },
    { key: "exit_reason", label: "reason", render: (t) => <span className="text-zinc-500">{t.exit_reason ?? ","}</span> },
    { key: "notional", label: "notional", align: "right", render: (t) => <span className="text-zinc-300">{fmtUsd(t.notional)}</span> },
    {
      key: "return_pct",
      label: "return",
      align: "right",
      render: (t) => <span className={pnlClass(t.return_pct)}>{t.return_pct !== null ? fmtPct(t.return_pct) : ","}</span>,
    },
    {
      key: "realized_pnl",
      label: "pnl",
      align: "right",
      render: (t) => (
        <span className={`font-semibold ${pnlClass(t.realized_pnl)}`}>
          {t.realized_pnl !== null ? fmtUsd(t.realized_pnl) : ","}
        </span>
      ),
    },
    { key: "fees", label: "fees", align: "right", render: (t) => <span className="text-zinc-500">{fmtUsd(t.fees)}</span> },
    { key: "bid_ratio_entry", label: "bid ratio", align: "right", render: (t) => <span className="text-zinc-400">{t.bid_ratio_entry !== null ? fmtNum(t.bid_ratio_entry, 2) : ","}</span> },
    { key: "spread_pct_entry", label: "spread%", align: "right", render: (t) => <span className="text-zinc-400">{t.spread_pct_entry !== null ? fmtNum(t.spread_pct_entry, 3) : ","}</span> },
    { key: "funding_entry", label: "funding", align: "right", render: (t) => <span className="text-zinc-400">{t.funding_entry !== null ? fmtNum(t.funding_entry, 6) : ","}</span> },
  ];

  function toggleSort(key: SortKey) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "desc" }));
  }

  function resetFilters() {
    setMode("all");
    setStrategy("all");
    setStatus("all");
    setSide("all");
    setCoin("all");
    setReason("all");
    setPage(0);
  }

  const selectCls =
    "rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-200 focus:border-zinc-500 focus:outline-none";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3 rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
        <Filter label="mode" value={mode} onChange={(v) => { setMode(v); setPage(0); }} options={["all", ...modes]} cls={selectCls} />
        <Filter label="strategy" value={strategy} onChange={(v) => { setStrategy(v); setPage(0); }} options={["all", ...strategies]} cls={selectCls} />
        <Filter label="status" value={status} onChange={(v) => { setStatus(v); setPage(0); }} options={["all", "open", "closed"]} cls={selectCls} />
        <Filter label="side" value={side} onChange={(v) => { setSide(v); setPage(0); }} options={["all", "long", "short"]} cls={selectCls} />
        <Filter label="coin" value={coin} onChange={(v) => { setCoin(v); setPage(0); }} options={["all", ...coins]} cls={selectCls} />
        <Filter label="exit reason" value={reason} onChange={(v) => { setReason(v); setPage(0); }} options={["all", ...reasons]} cls={selectCls} />
        <button type="button" onClick={resetFilters} className="rounded-md border border-zinc-700 px-2.5 py-1 text-xs text-zinc-400 hover:bg-zinc-800">
          reset
        </button>
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={() => download("trades.csv", toCsv(filtered), "text/csv")}
            className="rounded-md border border-zinc-700 px-2.5 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
          >
            export csv
          </button>
          <button
            type="button"
            onClick={() => download("trades.json", JSON.stringify(filtered, null, 2), "application/json")}
            className="rounded-md border border-zinc-700 px-2.5 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
          >
            export json
          </button>
        </div>
      </div>

      <div className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-900/50">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800 text-left text-xs lowercase tracking-wider text-zinc-500">
              {cols.map((c) => (
                <th
                  key={c.key}
                  onClick={() => toggleSort(c.key)}
                  className={`cursor-pointer select-none whitespace-nowrap px-3 py-2.5 font-medium hover:text-zinc-300 ${
                    c.align === "right" ? "text-right" : "text-left"
                  }`}
                >
                  {c.label}
                  {sort.key === c.key ? (sort.dir === "asc" ? " ↑" : " ↓") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="font-mono">
            {pageRows.length === 0 ? (
              <tr>
                <td colSpan={cols.length} className="px-3 py-10 text-center text-zinc-600">
                  no trades match these filters
                </td>
              </tr>
            ) : (
              pageRows.map((t) => (
                <tr key={t.id} className="border-b border-zinc-800/50 last:border-0 hover:bg-zinc-800/30">
                  {cols.map((c) => (
                    <td key={c.key} className={`whitespace-nowrap px-3 py-2 ${c.align === "right" ? "text-right" : "text-left"}`}>
                      {c.render(t)}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-zinc-500">
        <div className="flex items-center gap-2">
          <span>rows per page</span>
          <select
            value={pageSize}
            onChange={(e) => { setPageSize(Number(e.target.value)); setPage(0); }}
            className={selectCls}
          >
            {PAGE_SIZES.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
          <span className="ml-2">
            {filtered.length === 0 ? 0 : safePage * pageSize + 1}–
            {Math.min(filtered.length, safePage * pageSize + pageSize)} of {filtered.length}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <PagerBtn disabled={safePage === 0} onClick={() => setPage(0)}>{"«"}</PagerBtn>
          <PagerBtn disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>{"‹"}</PagerBtn>
          <span className="px-2 font-mono text-zinc-400">{safePage + 1} / {pageCount}</span>
          <PagerBtn disabled={safePage >= pageCount - 1} onClick={() => setPage(safePage + 1)}>{"›"}</PagerBtn>
          <PagerBtn disabled={safePage >= pageCount - 1} onClick={() => setPage(pageCount - 1)}>{"»"}</PagerBtn>
        </div>
      </div>
    </div>
  );
}

function Filter({
  label,
  value,
  onChange,
  options,
  cls,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
  cls: string;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] lowercase tracking-wider text-zinc-500">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} className={cls}>
        {options.map((o) => (
          <option key={o} value={o}>{o}</option>
        ))}
      </select>
    </label>
  );
}

function PagerBtn({
  children,
  disabled,
  onClick,
}: {
  children: React.ReactNode;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="rounded-md border border-zinc-700 px-2 py-1 text-zinc-300 enabled:hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
    >
      {children}
    </button>
  );
}
