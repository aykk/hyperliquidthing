# Strategy Log — Hyperliquid Trading Bot

> Running record of every strategy we've tested, what the data showed, what we
> learned, and what's still open. Written so the project can be picked up cold
> (e.g. in Claude Code) without re-deriving the history.
>
> **Last updated:** 2026-06-27

---

## TL;DR — current state

- **Decoupled system:** Python bot (`bot/`) + Next.js dashboard (`dashboard/`),
  sharing a SQLite trade journal (`bot/data/trades.db`).
- **Directional OBI scalping (obi_v1 → v4) has NOT shown an edge.** Every variant
  is net-negative on closed trades. Root cause: top-of-book order-book-imbalance
  scalping has no edge for a non-colocated participant, and the current market is
  a low-microstructure-vol grind.
- **Pivoted to funding carry (`carry_v1`)** — a delta-neutral, market-neutral
  yield strategy that does NOT need volatility. This is now the **core**
  ("set-and-forget") direction, sized for **$1,000** capital.
- **Currently running (paper-on-mainnet):**
  - `carry.py paper` — the carry allocator ($1,000, ~11% blended funding APR).
  - `paper.py` (obi_v4) — kept as a directional baseline for comparison.
  - `caffeinate -dimsu` — keeps the Mac awake (lid must stay OPEN).

---

## Market context (as of 2026-06-27)

Pulled live from the HL API. **Regime: clearly bearish.**

| coin | 24h | 7d | 30d | daily vol (14d) | funding APR |
|------|-----|-----|------|-----------------|-------------|
| BTC | +0.75% | −5.84% | −17.75% | 1.57% | +7.2% |
| ETH | +0.94% | −8.50% | −20.74% | 2.30% | −7.4% |
| SOL | +0.31% | −1.56% | −12.18% | 2.94% | +2.66% |
| DOGE | −0.45% | −9.88% | −24.26% | 1.60% | −4.04% |

Key insight: real, tradeable moves exist on the **daily** timescale (−12% to −24%
monthly), but the **tick/top-of-book** timescale the OBI bot scalps is just noise.
Funding sits near HL's ~10.9% baseline for many coins → carry is collectable.

---

## Architecture map

| File | Role |
|------|------|
| `bot/config.py` | All env-driven knobs (risk, sizing, strategy params). |
| `bot/screener.py` | REST scan → ranks tradeable perps (vol/funding/spread filters). |
| `bot/strategy.py` | OBI signal computation + exit logic. |
| `bot/execution.py` | Risk checks + order routing (isolated margin, rate limits, SL/TP). |
| `bot/paper.py` | Paper engine: simulates fills vs **real** mainnet data; journals trades. |
| `bot/live.py` | Live engine: places **real** orders on testnet/mainnet. |
| `bot/carry.py` | Funding-carry scanner + delta-neutral paper allocator. |
| `bot/journal.py` | SQLite trade journal (single source of truth, paper + live). |
| `bot/monitor.py` | Read-only live wallet monitor (real on-chain state). |
| `bot/botctl.py` | Start/stop/status CLI for the bot process. |
| `dashboard/` | Next.js dashboard: live portfolio + journal analytics + strategy compare. |

**Two data universes (a frequent source of confusion):**
1. **Real wallet** — `monitor.py` and the dashboard's *top* section (account, positions,
   fills) read the real on-chain account via the HL API / `webData2` WS.
2. **Simulation** — the dashboard's *strategy performance* + *trades* sections read the
   paper journal. In **paper mode no real orders are placed**, so the real wallet stays
   flat and the two universes intentionally differ. They only converge in live mode.

---

## Strategy ledger

### obi_v1 — maker-only OBI scalp
- **Thesis:** rest a post-only (Alo) order at the passive side on an order-book
  imbalance; earn the spread / pay no taker fee.
- **Params:** `ENTRY_MODE=maker`, thresholds 0.70/0.30, SL/TP 1.5%.
- **Result (live testnet):** 3 closed · 67% win · **net −$0.021** · −$0.007/trade.
- **Learning:** maker fills are rare (~5%) and **adversely selected** — we get
  filled right before the price moves against us. This is the exact failure mode
  that makes homemade market-making non-viable for us (see Infra learnings).

### obi_v2 — taker OBI scalp
- **Thesis:** if maker fills are rare/toxic, cross the spread (IOC) so the signal
  is actually tested.
- **Params:** `ENTRY_MODE=taker`.
- **Result (live testnet):** 3 closed · 0% win · **net −$0.068** · −$0.023/trade.
- **Learning:** fills happen, but each entry pays spread + taker fee, and the
  signal-flip exit scalps out on sub-cost moves → bleeds on costs.

### obi_v3 — hybrid entry + cost/flow/regime gates
- **Thesis:** capture maker economics when possible (post-only first, taker-chase
  if unfilled), and only enter on confirmed conditions.
- **Params:** `ENTRY_MODE=hybrid`, spread gate, aggressor-flow confirmation,
  liquidity floor, exit hysteresis + min hold.
- **Result (paper mainnet):** 5 closed · 0% win · **net −$0.106** · −$0.021/trade.
- **Learning:** better entries didn't fix the core problem — it still scalped out
  of positions before they cleared costs. Negative expectancy persisted.

### obi_v4 — "let winners run"
- **Thesis:** stop scalping. Disable the signal-flip exit; manage purely by
  SL / TP / trailing stop so a winner can run far enough to dwarf fees.
- **Params:** `DISABLE_FLIP_EXIT=true`, `STOP_LOSS_PCT=0.01`, `TAKE_PROFIT_PCT=0.015`,
  `TRAIL_ACTIVATE_PCT=0.004`, `TRAIL_PCT=0.0025`.
- **Result (paper mainnet):** 13 closed · 38% win · **net −$0.616** · −$0.047/trade.
- **Learning:** *worst per-trade so far.* In a flat/choppy microstructure regime
  there's no trend to "run," so it mostly hits the (wider) stop. Also opens
  **longs into a downtrend** because it's direction-symmetric — fighting the tape.
- **Note:** its open positions often show green *unrealized* PnL, which can make it
  look like the winner. Judge on **closed** expectancy, not live uPnL.

### carry_v1 — delta-neutral funding carry **(current core)**
- **Thesis:** ignore price direction. Collect the perpetual funding payment with a
  delta-neutral position (funding>0 → short perp + long spot). Edge is structural,
  not directional → **doesn't need volatility**.
- **Implementation:** `carry.py` is now a **set-and-forget allocator**:
  - Sizes a capital budget (`CARRY_CAPITAL`, currently $1,000) across the best
    net-APR, liquid, hedgeable, positive-funding coins.
  - Accrues funding hourly; **rebalances on a timer** — exits carries whose funding
    decayed/flipped, rotates capital into better ones.
  - Risk caps: max positions, per-coin %, max deployment (cash buffer), liquidity
    floor, min-net-APR floor.
- **Economics (measured):** majors/large-caps sit near HL's **~10.9% baseline
  funding** → **~8% net APR over a 30-day hold**, but **negative on short holds**
  (round-trip cost ≈ 0.23%, break-even ≈ 7–8 days). So carry is a **multi-week**
  play, modest but positive-expectancy. On $1,000 ≈ $80–110/yr.
- **Status:** running in paper-on-mainnet ($1,000, ~11% blended APR, 3 positions).

---

## Cross-cutting learnings

1. **OBI top-of-book scalping ≈ no edge for us.** Four variants, all net-negative.
   The signal is ~coin-flip gross and costs dominate. More exit tuning won't fix a
   break-even-gross signal.
2. **Costs are the whole game at small size.** Round-trip fees (~0.09% perp taker
   r/t, ~0.23% for a carry pair) dwarf the tiny moves being captured. Any viable
   strategy must have a per-trade edge bigger than its round-trip cost.
3. **Direction-symmetric scalping fights the trend.** In a −17% month, taking longs
   and shorts equally guarantees the longs get run over.
4. **Judge on closed-trade expectancy, not unrealized PnL.** Open positions flatter
   the dashboard.
5. **Market-neutral carry is the first thing with positive expectancy** — because
   its edge is structural (funding), not a directional bet.

## Infrastructure learnings

- **Laptop sleep kills runs.** `caffeinate -is` does NOT stop lid-close sleep; the
  process is suspended and the WS dies. **Keep the lid open** (and plugged in), or
  move to an always-on host (cheap VPS) for true multi-day runs.
- **The WS had no auto-reconnect** → on any drop the bot went silently blind while
  still "running." Fixed: both `paper.py` and `live.py` now run a 60s staleness
  watchdog that rebuilds the socket and resubscribes (without re-adopting positions).
- **Homemade HFT market-making is not viable for us**, even with a Tokyo VPS:
  - Our stack is Python + public API (ms-scale); real MMs are colocated Rust/C++
    (µs-scale). Colocation fixes geography, not the stack.
  - The "0.003% maker rebate" needs a high volume/staking tier; base tier *pays*
    0.015% to make.
  - Adverse selection is *worst* on majors (most competition) — proven by obi_v1.
  - The passive way to get MM-style yield is depositing into the **HLP vault**, not
    running your own MM.
- **HL API is free** (rate-limited, ~10k request buffer). Keeping a script open
  costs nothing.

---

## Decisions made

- Stay in **Unified account mode** (shared collateral); set positions isolated within it.
- **Paper-on-mainnet** is the evaluation harness: real data, simulated fills, risk-free.
- Per-strategy budget convention; A/B via `STRATEGY_NAME` tag in the journal.
- **Core strategy = funding carry** for the $1,000 (set-and-forget, market-neutral).
- obi_v4 kept running only as a directional baseline, not a deployment candidate.
- **Dropped** homemade market-making.

---

## Open questions

1. **Carry live viability:** does ~8% net APR survive *real* execution (actual
   spot+perp fills, slippage, basis, the short-perp margin/liquidation risk)? Needs a
   small live-mainnet test after the paper allocator validates.
2. **Hedge leg mechanics on HL:** confirm exact spot markets used to hedge each perp
   (U-wrapped majors: UBTC/UETH/USOL), spot fees, and whether unified collateral lets
   the spot long fully margin the perp short.
3. **Funding persistence:** how stable is the ~10.9% baseline? How often does it flip
   negative and force a rebalance/exit? (The allocator handles it; we lack long data.)
4. **Cross-exchange carry (HL vs Binance):** higher alt yields but needs a 2nd venue +
   capital split + 2-leg liquidation management. Worth it at $1k? Probably not yet.
5. **Directional, done right:** would a *higher-timeframe, trend-aligned* swing trader
   (hold hours–days, short the bounces in a downtrend, ATR stops) have an edge where
   tick-scalping doesn't? Untested.
6. **HLP vault** as a passive allocation/benchmark — track its realized APR vs our carry.

---

## How to run / operate

```bash
# --- scanner (read-only): rank live funding-carry opportunities ---
cd bot && IS_MAINNET=true ./.venv/bin/python carry.py scan --min-vol 20 --top 15

# --- carry allocator (paper-on-mainnet, $1,000) ---
cd bot && IS_MAINNET=true CARRY_CAPITAL=1000 ./.venv/bin/python carry.py paper

# --- live wallet monitor (real on-chain state) ---
cd bot && IS_MAINNET=true ./.venv/bin/python monitor.py

# --- OBI bot control (paper/live) ---
cd bot && ./.venv/bin/python botctl.py status|stop

# --- keep the Mac awake for long runs (lid MUST stay open) ---
caffeinate -dimsu &

# --- dashboard ---
cd dashboard && npm run dev   # http://localhost:3000
```

Carry allocator knobs (env): `CARRY_CAPITAL`, `CARRY_MAX_POSITIONS`,
`CARRY_MIN_NET_APR`, `CARRY_HOLD_HORIZON_DAYS`, `CARRY_MIN_VOL_M`,
`CARRY_PER_COIN_PCT`, `CARRY_MAX_DEPLOY_PCT`, `CARRY_REBALANCE_SEC`.

---

## Roadmap (suggested next steps)

1. Let the $1,000 carry allocator run multi-day; confirm equity crosses break-even
   (~day 7–8) and trends up at ~the blended APR.
2. Add a **carry panel** to the dashboard (reads `carry_status.json`): capital,
   deployed, blended APR, per-coin accrued, PnL vs break-even.
3. Small **live-mainnet** carry test (1–2 positions, real fills) to validate the
   paper assumptions (fees/slippage/basis).
4. Optional: HLP vault tracking; cross-exchange carry research; trend-swing experiment.
