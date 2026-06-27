import type { Fill, OpenOrder } from "@/lib/types";
import { fmtPx, fmtTime, fmtUsd, fmtNum } from "@/lib/format";

function OpenOrders({ orders }: { orders: OpenOrder[] }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
      <h2 className="mb-4 text-sm font-semibold lowercase tracking-wider text-zinc-400">
        open orders
      </h2>
      {orders.length === 0 ? (
        <p className="py-6 text-center text-sm text-zinc-600">no resting orders</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-left text-xs lowercase tracking-wider text-zinc-500">
                <th className="pb-2 pr-4 font-medium">asset</th>
                <th className="pb-2 pr-4 font-medium">side</th>
                <th className="pb-2 pr-4 text-right font-medium">price</th>
                <th className="pb-2 pr-4 text-right font-medium">size</th>
                <th className="pb-2 text-right font-medium">action</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {orders.map((o) => (
                <tr key={o.oid} className="border-b border-zinc-800/50 last:border-0">
                  <td className="py-2.5 pr-4 font-sans font-semibold text-zinc-100">{o.coin}</td>
                  <td className={`py-2.5 pr-4 ${o.side === "B" ? "text-emerald-400" : "text-red-400"}`}>
                    {o.side === "B" ? "buy" : "sell"}
                  </td>
                  <td className="py-2.5 pr-4 text-right text-zinc-300">{fmtPx(o.limitPx)}</td>
                  <td className="py-2.5 pr-4 text-right text-zinc-300">{fmtNum(o.sz)}</td>
                  <td className="py-2.5 text-right">
                    <button
                      type="button"
                      title="cancelling requires a signed action; the read-only dashboard cannot sign. wire to a backend to enable."
                      disabled
                      className="cursor-not-allowed rounded border border-zinc-700 px-2 py-0.5 text-xs text-zinc-600"
                    >
                      cancel
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function TradeHistory({ fills }: { fills: Fill[] }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
      <h2 className="mb-4 text-sm font-semibold lowercase tracking-wider text-zinc-400">
        trade history <span className="text-zinc-600">(last {fills.length})</span>
      </h2>
      {fills.length === 0 ? (
        <p className="py-6 text-center text-sm text-zinc-600">no fills yet</p>
      ) : (
        <div className="max-h-72 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-zinc-900">
              <tr className="border-b border-zinc-800 text-left text-xs lowercase tracking-wider text-zinc-500">
                <th className="pb-2 pr-4 font-medium">time</th>
                <th className="pb-2 pr-4 font-medium">asset</th>
                <th className="pb-2 pr-4 font-medium">side</th>
                <th className="pb-2 pr-4 text-right font-medium">price</th>
                <th className="pb-2 text-right font-medium">realized pnl</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {fills.map((f, i) => (
                <tr key={`${f.time}-${i}`} className="border-b border-zinc-800/50 last:border-0">
                  <td className="py-2 pr-4 text-zinc-500">{fmtTime(f.time)}</td>
                  <td className="py-2 pr-4 font-sans font-semibold text-zinc-100">{f.coin}</td>
                  <td className={`py-2 pr-4 ${f.side === "B" ? "text-emerald-400" : "text-red-400"}`}>
                    {f.side === "B" ? "buy" : "sell"}
                  </td>
                  <td className="py-2 pr-4 text-right text-zinc-300">{fmtPx(f.px)}</td>
                  <td
                    className={`py-2 text-right font-semibold ${
                      f.closedPnl > 0 ? "text-emerald-400" : f.closedPnl < 0 ? "text-red-400" : "text-zinc-500"
                    }`}
                  >
                    {f.closedPnl !== 0 ? fmtUsd(f.closedPnl) : ","}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function OrdersAndHistory({ orders, fills }: { orders: OpenOrder[]; fills: Fill[] }) {
  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
      <OpenOrders orders={orders} />
      <TradeHistory fills={fills} />
    </div>
  );
}
