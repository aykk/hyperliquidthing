# Live Monitoring Dashboard Specification

> **Status:** reflects current implementation (2026-06-27). Grew from a single page
> into a multi-page app ("hyperliquid perps bot 0.1", all-lowercase, dark mode).

## 1. Tech Stack & Architecture
* **Framework:** Next.js (App Router) + TypeScript.
* **Styling:** Tailwind CSS (dark trading-terminal theme).
* **State:** React Hooks; live data via native WebSocket, journal/bot state via
  internal API routes.
* **Principle:** the frontend connects **directly** to Hyperliquid for market/account
  data and never routes through the Python bot. Only the public address is used
  client-side — no private key in the browser.

## 2. Two data sources (important)
1. **Real on-chain account** — `useHyperliquidData.ts` subscribes to `webData2`
   (account/margin/positions) and `userFills` for `NEXT_PUBLIC_MAIN_ACCOUNT_ADDRESS`.
   Powers the live-portfolio top section. Mirrors `bot/monitor.py`.
2. **Paper journal (simulation)** — `usePaperData.ts` polls `/api/paper`, which reads
   the SQLite journal. Powers the analytics/trades. In paper mode the real account
   is untouched, so these two sources intentionally differ.

Total equity (unified account) = `perpsAccountValue + spotUsdc` to match the
`webData2` accounting model. Auto-reconnect on WS drop.

## 3. Pages (global `Navbar`)
### `/` — live portfolio
* **`Header`** — connection status, address, network.
* **`BotControl`** — start/stop the bot + status (reads/writes `/api/bot`); label
  reflects the running network/mode.
* **`AccountOverview`** — total equity, available margin, margin-used %, spot/perps split.
* **`PositionsTable`** — open positions: coin, notional, entry, mark, liq price,
  unrealized PnL (green/red).
* **`OrdersAndHistory`** — resting open orders + recent fills.
* **Strategy performance (journal):** **`StrategySelect`** segmented control
  (`all` + each strategy) filters **`EquityCurve`** and **`PerformanceSummary`**
  (net PnL, win rate, expectancy, drawdown, fees, avg win/loss, breakdowns) —
  all recomputed client-side (`lib/stats.ts`) for the selected strategy.

### `/trades`
* **`StrategyComparison`** — per-strategy·mode table (closed, win%, net, expectancy),
  normalizing paper(net) vs live(gross) PnL semantics.
* **`TradesTable`** — full filterable/sortable journal.

### `/strategy`
* **`StrategyTabs`** — definitions/status/params for each strategy variant
  (obi_v1–v4 thesis + parameters; carry tracked separately).

## 4. API routes
* **`/api/paper`** — reads the SQLite journal → `{ ready, stats, trades }`.
* **`/api/bot`** — GET status (from the heartbeat status file) / POST start|stop
  (spawns or signals the detached Python process).

## 5. UI/UX
* Dark mode, all-lowercase copy, monospace numerics, smooth live updates (no manual
  refresh), auto-reconnect on WebSocket disconnect.

## 6. Not yet implemented
* A **carry panel** reading `bot/data/carry_status.json` (capital, deployed, blended
  APR, per-coin accrued, PnL vs break-even) — see `../STRATEGY_LOG.md` roadmap.
