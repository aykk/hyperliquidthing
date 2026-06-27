import type { Metadata } from "next";
import { StrategyTabs } from "@/components/StrategyTabs";

export const metadata: Metadata = {
  title: "strategy · hyperliquid perps bot 0.1",
};

export default function StrategyPage() {
  return (
    <main className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-6 p-4 sm:p-6">
      <header className="flex flex-wrap items-end justify-between gap-3 border-b border-zinc-800 pb-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-zinc-100">strategy</h1>
          <p className="font-mono text-xs text-zinc-500">
            4 independent strategy slots · manually maintained
          </p>
        </div>
      </header>

      <StrategyTabs />
    </main>
  );
}
