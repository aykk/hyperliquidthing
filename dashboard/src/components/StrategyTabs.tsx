"use client";

// strategy definitions , MANUALLY MAINTAINED (not wired to the bot).
// Each of the 4 slots is an independent record so iterating on strategy 2/3/4
// never overwrites strategy 1. Set STRATEGY_NAME in .env to the `name` of the
// slot you're currently running so its trades are tagged to match.

import { useState } from "react";

type ParamGroup = { group: string; rows: [string, string][] };

interface StrategyDef {
  name: string;
  version: string;
  lastUpdated: string;
  status: "active" | "paused" | "draft";
  thesis: string;
  params: ParamGroup[];
  openQuestions: string[];
}

const OBI_V1: StrategyDef = {
  name: "obi_v1",
  version: "v0.1",
  lastUpdated: "2026-06-26",
  status: "paused",
  thesis: `trades short-horizon order-book imbalance on the most liquid hyperliquid
perps (btc, eth, sol, doge on testnet). when resting bid liquidity dominates the top
of book it leans long (and vice-versa), enters passively as a maker, and exits on a
symmetric ±1.5% bracket or when the imbalance edge disappears. sized as a ~$240
account so the ~1000-usdc wallet can fund ~4 parallel strategy experiments.
PAUSED: maker-only entries filled on only ~5% of submitted orders, so the signal
was never really tested , superseded by obi_v2 (taker entries) for live data.`,
  params: [
    {
      group: "budget & sizing",
      rows: [
        ["budget per strategy", "$240 (4 strategies ≈ $960 of ~1000, ~$40 for fees)"],
        ["notional per trade", "$25 (1x isolated)"],
        ["max concurrent positions", "5 → $125 max exposure"],
        ["leverage", "1x isolated"],
      ],
    },
    {
      group: "universe",
      rows: [
        ["testnet", "fixed: btc, eth, sol, doge (volume screener is meaningless on testnet)"],
        ["mainnet", "screener: 24h vol > $10m, abs(funding) < 0.0001, spread < 0.08%"],
      ],
    },
    {
      group: "entry signal (order-book imbalance)",
      rows: [
        ["feature", "bid_ratio = bid$ / (bid$ + ask$) over top 10 levels"],
        ["long", "bid_ratio ≥ 0.70"],
        ["short", "bid_ratio ≤ 0.30"],
        ["neutral", "0.30 < bid_ratio < 0.70 (no trade)"],
        ["order type", "post-only (maker) at the passive side; 15s tif then cancel"],
      ],
    },
    {
      group: "exits",
      rows: [
        ["take-profit", "+1.5% (maker limit exit)"],
        ["stop-loss", "−1.5% hard stop (taker market)"],
        ["signal-flip", "exit when bid_ratio reverts through 0.50 (edge gone)"],
        ["risk:reward", "~1:1"],
      ],
    },
    {
      group: "guardrails",
      rows: [
        ["api write budget", "≤ 30 orders / hour"],
        ["state source of truth", "live exchange state (never local)"],
        ["margin", "isolated per-position; default account mode"],
        ["emergency flatten", "cancel all + market-close all on heartbeat loss > 5s"],
      ],
    },
  ],
  openQuestions: [
    "does the obi signal have positive expectancy after maker adverse selection + fees?",
    "are 0.70/0.30 the right thresholds, or should they adapt to volatility?",
    "is a symmetric 1.5% bracket optimal vs. a wider tp / tighter sl?",
    "should sizing scale with conviction (distance of bid_ratio from 0.5)?",
  ],
};

const OBI_V2: StrategyDef = {
  name: "obi_v2",
  version: "v0.2",
  lastUpdated: "2026-06-26",
  status: "paused",
  thesis: `same order-book-imbalance signal as obi_v1, but entries CROSS THE SPREAD as
a marketable ioc (taker) instead of resting post-only. obi_v1 only filled ~5% of its
orders, so its edge was never actually tested; obi_v2 trades the fill-rate problem for
near-100% fills at the cost of paying the spread + taker fee per entry. the point is to
generate enough closed trades to measure whether the imbalance signal has real
expectancy once it's reliably in the market. everything else (universe, ±1.5% bracket,
sizing, guardrails) is held constant so v1 vs v2 is a clean a/b on execution style.
PAUSED: early fills exited 100% on signal-flips after sub-0.1% moves while paying
~0.06-0.09% round-trip , costs ate the move. learnings folded into obi_v3.`,
  params: [
    {
      group: "budget & sizing",
      rows: [
        ["budget per strategy", "$240 (4 strategies ≈ $960 of ~1000, ~$40 for fees)"],
        ["notional per trade", "$25"],
        ["max concurrent positions", "5 → $125 max exposure"],
        ["leverage / margin", "1x, cross (unified account)"],
      ],
    },
    {
      group: "universe",
      rows: [
        ["testnet", "fixed: btc, eth, sol, doge (same as obi_v1)"],
        ["mainnet", "screener: 24h vol > $10m, abs(funding) < 0.0001, spread < 0.08%"],
      ],
    },
    {
      group: "entry signal (order-book imbalance)",
      rows: [
        ["feature", "bid_ratio = bid$ / (bid$ + ask$) over top 10 levels"],
        ["long", "bid_ratio ≥ 0.70"],
        ["short", "bid_ratio ≤ 0.30"],
        ["neutral", "0.30 < bid_ratio < 0.70 (no trade)"],
        ["order type", "taker , marketable ioc that crosses the spread"],
        ["slippage cap", "≤ 0.1% past the touch (MAX_TAKER_SLIPPAGE_PCT)"],
      ],
    },
    {
      group: "exits",
      rows: [
        ["take-profit", "+1.5% (maker limit exit)"],
        ["stop-loss", "−1.5% hard stop (taker market)"],
        ["signal-flip", "exit when bid_ratio reverts through 0.50 (edge gone)"],
        ["risk:reward", "~1:1 before fees (taker entry fee now matters more)"],
      ],
    },
    {
      group: "guardrails",
      rows: [
        ["api write budget", "≤ 30 orders / hour"],
        ["state source of truth", "live exchange state (never local)"],
        ["margin", "cross; unified account collateral"],
        ["emergency flatten", "cancel all + market-close all on heartbeat loss > 5s"],
      ],
    },
  ],
  openQuestions: [
    "with reliable fills, does obi actually have positive expectancy net of taker fees + the spread paid on entry?",
    "how does v2's win-rate / avg-pnl compare to the handful of v1 maker fills?",
    "is the +1.5%/−1.5% bracket too tight now that each entry starts ~1 spread underwater?",
    "would a hybrid (post-only first, cross only if unfilled after N seconds) beat pure taker?",
  ],
};

const OBI_V3: StrategyDef = {
  name: "obi_v3",
  version: "v0.3",
  lastUpdated: "2026-06-27",
  status: "paused",
  thesis: `addresses the core finding from v1/v2: every trade exited on a signal-flip
after a sub-0.1% move while round-trip costs were ~0.06-0.09%, so costs ate the edge,
and the two strong-signal taker shorts were adversely selected. v3 keeps the imbalance
signal but (1) widens entry thresholds to 0.75/0.25 for higher conviction, (2) adds
exit hysteresis + a 10s minimum hold so winners can clear costs instead of being cut on
noise, (3) gates out wide-spread entries, (4) uses hybrid execution (rest as a maker,
cross as a taker only if unfilled) to capture maker economics when possible, and (5) on
MAINNET adds aggressor-flow confirmation + a liquidity floor so we only trade when real
order flow agrees and the tape isn't dead. tuned for the june 2026 regime: range-bound,
thin liquidity, high iv, choppy.
PAUSED: even with hysteresis + 10s min-hold, paper-on-mainnet trades still exited as
sub-cent signal-flips that lost to the round-trip fee. the min-hold delayed the cut but
didn't change that the move was smaller than the cost , superseded by obi_v4, which
stops cutting on the flip entirely and lets winners run under a trailing stop.`,
  params: [
    {
      group: "budget & sizing",
      rows: [
        ["budget per strategy", "$240"],
        ["notional per trade", "$25"],
        ["max concurrent positions", "5 → $125 max exposure"],
        ["leverage / margin", "1x, cross (unified account)"],
      ],
    },
    {
      group: "entry signal (order-book imbalance)",
      rows: [
        ["feature", "bid_ratio = bid$ / (bid$ + ask$) over top 10 levels"],
        ["long", "bid_ratio ≥ 0.75 (was 0.70)"],
        ["short", "bid_ratio ≤ 0.25 (was 0.30)"],
        ["order type", "hybrid , post-only first, cross as taker if unfilled in 5s"],
        ["spread gate", "skip entries when top-of-book spread > 0.05%"],
      ],
    },
    {
      group: "flow confirmation (mainnet only)",
      rows: [
        ["source", "public trades stream , realized aggressor flow, 30s window"],
        ["confirm", "require ≥ 55% of flow on the signal's side before entering"],
        ["liquidity floor", "skip if windowed aggressor notional < threshold"],
        ["testnet", "OFF (no real flow to confirm against)"],
      ],
    },
    {
      group: "exits",
      rows: [
        ["take-profit", "+1.5% (maker limit exit)"],
        ["stop-loss", "−1.5% hard stop (taker market)"],
        ["signal-flip", "hysteresis: long exits < 0.45, short exits > 0.55"],
        ["minimum hold", "10s before a signal-flip exit is allowed (bracket exempt)"],
      ],
    },
    {
      group: "guardrails",
      rows: [
        ["api write budget", "≤ 30 orders / hour"],
        ["state source of truth", "live exchange state (never local)"],
        ["margin", "cross; unified account collateral"],
        ["emergency flatten", "cancel all + market-close all on heartbeat loss > 5s"],
      ],
    },
  ],
  openQuestions: [
    "does hysteresis + min-hold raise avg win enough to clear the ~0.06-0.09% round-trip cost?",
    "on mainnet, does aggressor-flow confirmation actually filter the adverse-selected entries?",
    "is the 0.05% spread gate too strict (too few fills) or about right?",
    "is 10s the right hold, or should it adapt to realized volatility?",
    "does hybrid execution meaningfully cut fees vs pure taker without killing fill rate?",
  ],
};

const OBI_V4: StrategyDef = {
  name: "obi_v4",
  version: "v0.4",
  lastUpdated: "2026-06-27",
  status: "paused",
  thesis: `v1→v3 all shared one fatal trait: they scalped OUT of every position the moment
the (noisy) order-book imbalance reverted, capturing sub-0.1% moves while paying a
~0.06-0.09% round-trip , structurally losing to fees. v4's thesis: stop scalping. keep
v3's high-conviction entries (0.75/0.25 thresholds, hybrid execution, spread gate,
mainnet flow confirmation) but change the EXIT: disable the signal-flip cut entirely and
let a winner run, protecting it with a trailing stop that locks gains while the trend
extends. losers are cut quickly by a tighter -1.0% hard stop; winners ride until they
give back 0.25% from their peak (armed only after +0.4%), capped at +1.5%. the goal is an
asymmetric payoff where the occasional runner more than pays for the small losers + fees.`,
  params: [
    {
      group: "budget & sizing",
      rows: [
        ["budget per strategy", "$240"],
        ["notional per trade", "$25"],
        ["max concurrent positions", "5 → $125 max exposure"],
        ["leverage / margin", "1x, cross (unified account)"],
      ],
    },
    {
      group: "entry signal (inherited from obi_v3)",
      rows: [
        ["feature", "bid_ratio = bid$ / (bid$ + ask$) over top 10 levels"],
        ["long / short", "bid_ratio ≥ 0.75 / ≤ 0.25"],
        ["order type", "hybrid , post-only first, cross as taker if unfilled in 5s"],
        ["spread gate", "skip entries when top-of-book spread > 0.05%"],
        ["flow confirm (mainnet)", "require ≥ 55% aggressor flow on the signal's side (30s window)"],
      ],
    },
    {
      group: "exits , let winners run",
      rows: [
        ["signal-flip", "DISABLED , no longer cut when the imbalance reverts"],
        ["trailing stop", "ride 0.25% behind the peak, armed after +0.4% in favor"],
        ["take-profit", "+1.5% hard cap (maker limit)"],
        ["stop-loss", "−1.0% hard stop (taker market) , cut losers fast"],
        ["payoff shape", "asymmetric: small capped losses, uncapped trailing winners"],
      ],
    },
    {
      group: "guardrails",
      rows: [
        ["api write budget", "≤ 30 orders / hour"],
        ["state source of truth", "live exchange state (never local)"],
        ["margin", "cross; unified account collateral"],
        ["emergency flatten", "cancel all + market-close all on heartbeat loss > 5s"],
      ],
    },
  ],
  openQuestions: [
    "does disabling the flip-cut + trailing stop produce a positive avg-net-per-trade where v3 was negative?",
    "is +0.4% the right trail-arm point, or does it give back too much before locking in?",
    "is the 0.25% trail too tight (whipsawed out of runners) or too loose (gives back gains)?",
    "does a tighter -1.0% stop whipsaw more than the -1.5% it replaced?",
    "what fraction of trades actually become runners vs. small stop-outs in this regime?",
  ],
};

const CARRY_V1: StrategyDef = {
  name: "carry_v1",
  version: "v1.0",
  lastUpdated: "2026-06-27",
  status: "active",
  thesis: `set-and-forget delta-neutral funding carry , the core strategy after obi_v1–v4
showed no directional edge. when funding is positive, longs pay shorts: short the perp and
long equal-notional spot (delta-neutral , price moves cancel, you keep the funding). the
allocator sizes a capital budget ($1,000) across the best net-APR, liquid, hedgeable coins;
accrues funding hourly; and rebalances on a timer (exit funding-decayed carries, rotate into
better ones). ~8–11% net APR over multi-week holds; break-even on entry costs ~7–8 days.`,
  params: [
    {
      group: "allocator policy",
      rows: [
        ["capital", "$1,000 (CARRY_CAPITAL)"],
        ["max positions", "4"],
        ["per-coin cap", "35% of capital"],
        ["max deploy", "90% (10% cash buffer)"],
        ["min net APR @ 30d", "3%"],
        ["rebalance", "every 60 minutes"],
      ],
    },
    {
      group: "mechanics",
      rows: [
        ["edge source", "structural funding payment (not directional)"],
        ["position", "short perp + long spot (funding>0)"],
        ["round-trip cost", "~0.23% (both legs, entry+exit)"],
        ["break-even", "~7–8 days at ~11% funding APR"],
        ["evaluation", "paper-on-mainnet (real funding, simulated fills)"],
      ],
    },
    {
      group: "guardrails",
      rows: [
        ["liquidity floor", "24h vol ≥ $20M"],
        ["hedge requirement", "matching HL spot market must exist"],
        ["exit trigger", "funding flip, decay below floor, or fall out of top set"],
        ["dashboard", "live portfolio → funding carry panel"],
      ],
    },
  ],
  openQuestions: [
    "does ~8% net APR survive real execution (slippage, basis, spot+perp fees)?",
    "how often does funding flip and force a rebalance?",
    "when to graduate from paper to small live-mainnet ($1k)?",
    "cross-exchange carry (HL vs Binance) , worth it at this capital level?",
  ],
};

const STRATEGIES: StrategyDef[] = [OBI_V1, OBI_V2, OBI_V3, OBI_V4, CARRY_V1];

export function StrategyTabs() {
  const defaultTab = Math.max(0, STRATEGIES.findIndex((st) => st.status === "active"));
  const [active, setActive] = useState(defaultTab);
  const s = STRATEGIES[active];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap gap-2 border-b border-zinc-800 pb-3">
        {STRATEGIES.map((st, i) => (
          <button
            key={st.name}
            type="button"
            onClick={() => setActive(i)}
            className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm lowercase transition-colors ${
              i === active
                ? "border-zinc-600 bg-zinc-800 text-zinc-100"
                : "border-zinc-800 bg-zinc-900/40 text-zinc-500 hover:text-zinc-300"
            }`}
          >
            strategy {i + 1}
            <span className="font-mono text-[11px] text-zinc-500">{st.name}</span>
            {st.status === "active" && <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />}
          </button>
        ))}
      </div>

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 font-mono text-xs">
          <span className="rounded bg-zinc-800 px-2 py-0.5 text-zinc-300">{s.version}</span>
          <span className="text-zinc-600">updated {s.lastUpdated}</span>
          <span
            className={`rounded px-2 py-0.5 ${
              s.status === "active" ? "bg-emerald-950/60 text-emerald-400" : "bg-zinc-800 text-zinc-500"
            }`}
          >
            {s.status}
          </span>
        </div>
      </div>

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h2 className="mb-2 text-sm font-semibold lowercase tracking-wider text-zinc-400">thesis</h2>
        <p className="text-sm leading-relaxed text-zinc-300">{s.thesis}</p>
      </section>

      {s.params.map((p) => (
        <section key={p.group} className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
          <h2 className="mb-3 text-sm font-semibold lowercase tracking-wider text-zinc-400">{p.group}</h2>
          <dl className="divide-y divide-zinc-800/70">
            {p.rows.map(([k, v]) => (
              <div key={k} className="flex flex-col gap-1 py-2 sm:flex-row sm:items-baseline sm:justify-between">
                <dt className="text-sm text-zinc-400">{k}</dt>
                <dd className="font-mono text-sm text-zinc-200 sm:text-right">{v}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}

      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h2 className="mb-3 text-sm font-semibold lowercase tracking-wider text-zinc-400">
          open questions / iteration log
        </h2>
        <ul className="list-inside list-disc space-y-1.5 text-sm text-zinc-300">
          {s.openQuestions.map((q) => (
            <li key={q}>{q}</li>
          ))}
        </ul>
      </section>
    </div>
  );
}
