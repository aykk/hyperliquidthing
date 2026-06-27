"use client";

import { CarryPanel } from "@/components/CarryPanel";
import { AccountOverview } from "@/components/AccountOverview";
import { BotControl } from "@/components/BotControl";
import { LiveEquityCurve } from "@/components/LiveEquityCurve";
import { Header } from "@/components/Header";
import { OrdersAndHistory } from "@/components/OrdersAndHistory";
import { PositionsTable } from "@/components/PositionsTable";
import { useHyperliquidData } from "@/lib/useHyperliquidData";
import { useEquityHistory } from "@/lib/useEquityHistory";
import { useCarryData } from "@/lib/useCarryData";
import { fmtUsd } from "@/lib/format";

const IS_MAINNET = process.env.NEXT_PUBLIC_IS_MAINNET !== "false";

export default function LivePortfolio() {
  const { status, lastUpdate, account, positions, openOrders, fills, address } =
    useHyperliquidData();
  const carryState = useCarryData(true);
  const equityHistory = useEquityHistory(account?.totalEquity);

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-5 p-4 sm:p-6">
      <Header
        status={status}
        address={address}
        lastUpdate={lastUpdate}
        title="live portfolio"
        network={IS_MAINNET ? "mainnet" : "testnet"}
      />

      {!address && (
        <div className="rounded-xl border border-amber-800/50 bg-amber-950/30 p-4 text-sm text-amber-300">
          no <code className="font-mono">next_public_main_account_address</code> set in{" "}
          <code className="font-mono">dashboard/.env.local</code>. the dashboard needs your public
          address to stream account data.
        </div>
      )}

      <BotControl />

      <CarryPanel
        carry={carryState.data}
        lastUpdate={carryState.lastUpdate}
        liveTotalEquity={account?.totalEquity}
      />

      <AccountOverview account={account} />
      <PositionsTable positions={positions} />
      <OrdersAndHistory orders={openOrders} fills={fills} />

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold lowercase tracking-wider text-zinc-400">
            total equity <span className="text-zinc-600">(live wallet)</span>
          </h2>
          <span className="font-mono text-sm text-zinc-200">
            {fmtUsd(account?.totalEquity ?? null)}
          </span>
        </div>
        <p className="mb-3 text-xs text-zinc-600">
          your real hyperliquid account (perps + spot). paper strategy sims are on the trades page.
        </p>
        <LiveEquityCurve points={equityHistory} currentEquity={account?.totalEquity} />
      </section>
    </main>
  );
}
