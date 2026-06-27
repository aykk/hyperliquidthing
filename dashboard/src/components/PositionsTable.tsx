import type { Position } from "@/lib/types";
import { fmtPx, fmtUsd } from "@/lib/format";

function pnlClass(n: number) {
  return n > 0 ? "text-emerald-400" : n < 0 ? "text-red-400" : "text-zinc-400";
}

export function PositionsTable({ positions }: { positions: Position[] }) {
  return (
    <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
      <h2 className="mb-4 text-sm font-semibold lowercase tracking-wider text-zinc-400">
        active positions &amp; live pnl
      </h2>
      {positions.length === 0 ? (
        <p className="py-8 text-center text-sm text-zinc-600">no open positions</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-left text-xs lowercase tracking-wider text-zinc-500">
                <th className="pb-2 pr-4 font-medium">asset</th>
                <th className="pb-2 pr-4 text-right font-medium">size (usd)</th>
                <th className="pb-2 pr-4 text-right font-medium">entry</th>
                <th className="pb-2 pr-4 text-right font-medium">mark</th>
                <th className="pb-2 pr-4 text-right font-medium">liq.</th>
                <th className="pb-2 text-right font-medium">upnl</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {positions.map((p) => (
                <tr key={p.coin} className="border-b border-zinc-800/50 last:border-0">
                  <td className="py-2.5 pr-4">
                    <span className="font-sans font-semibold text-zinc-100">{p.coin}</span>
                    <span
                      className={`ml-2 rounded px-1.5 py-0.5 text-[10px] font-semibold lowercase ${
                        p.szi > 0
                          ? "bg-emerald-500/10 text-emerald-400"
                          : "bg-red-500/10 text-red-400"
                      }`}
                    >
                      {p.szi > 0 ? "long" : "short"}
                    </span>
                  </td>
                  <td className="py-2.5 pr-4 text-right text-zinc-300">{fmtUsd(p.notionalUsd)}</td>
                  <td className="py-2.5 pr-4 text-right text-zinc-300">{fmtPx(p.entryPx)}</td>
                  <td className="py-2.5 pr-4 text-right text-zinc-300">{fmtPx(p.markPx)}</td>
                  <td className="py-2.5 pr-4 text-right text-amber-400/80">{fmtPx(p.liquidationPx)}</td>
                  <td className={`py-2.5 text-right font-semibold ${pnlClass(p.unrealizedPnl)}`}>
                    {fmtUsd(p.unrealizedPnl)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
