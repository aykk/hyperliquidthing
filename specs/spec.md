# Master System Specification: Hyperliquid Bot

> **Status:** reflects the current implementation as of 2026-06-27. The original
> design targeted a $50 isolated-margin OBI scalper; the system has since evolved
> (see "Deviations from original design"). For the full strategy history and
> rationale, see `../STRATEGY_LOG.md`.

## 1. System Architecture
A decoupled, multi-module system. Two top-level processes that share a single
SQLite trade journal but never call each other directly:

* **Python backend (`bot/`)** — screening, signal generation, paper/live
  execution, risk, and the funding-carry allocator.
* **Next.js dashboard (`dashboard/`)** — read-only monitoring + journal analytics.
  Connects **directly** to Hyperliquid (never routes through the Python bot).

### Backend modules
* **Screener (`screener.py`, see `screener.md`):** REST scan that ranks the perp
  universe by liquidity/funding/spread filters. Optional — runs can also pin a
  fixed coin universe via the `COINS` env var.
* **Strategy Engine (`strategy.py`, see `strategy_execution.md`):** consumes the
  `l2Book` (+ `trades`) WebSocket streams and computes Order-Book-Imbalance signals.
* **Execution & Risk (`execution.py`):** validates and routes orders; enforces
  sizing, position caps, rate limits, and the protective SL/TP bracket.
* **Paper engine (`paper.py`):** simulates fills against **real** market data and
  writes to the journal. Primary evaluation harness ("paper-on-mainnet").
* **Live engine (`live.py`):** places real orders (testnet or mainnet); reconciles
  on-chain positions on startup.
* **Carry allocator (`carry.py`):** delta-neutral funding-carry scanner + paper
  allocator (the current core strategy).
* **Journal (`journal.py`):** SQLite single source of truth (WAL mode) for every
  paper/live trade, including entry-context features and a `strategy` tag.
* **Monitor (`monitor.py`) / control (`botctl.py`):** read-only wallet monitor and
  start/stop/status CLI.

## 2. Global System Constraints
* **Capital:** env-driven. Paper OBI runs use a per-strategy budget
  (`STARTING_EQUITY`, default $250); the carry allocator is sized via
  `CARRY_CAPITAL` (currently $1,000).
* **Margin Mode:** account is in **Unified** mode (shared collateral); positions
  are set isolated *within* the unified pool. (Original spec mandated isolated-only
  with cross banned — see deviations.)
* **Minimum Order Size:** $10.00 USD notional (protocol minimum).
* **Rate-limit mitigation:** the bot minimizes API writes. `MAX_ORDERS_PER_HOUR`
  (default 30) caps activity; a trade costs ~3 writes (entry + SL + TP).
* **State verification:** the live bot queries Hyperliquid state directly before
  executing; on startup it reconciles/adopts existing on-chain positions rather
  than trusting local state.
* **Resilience:** a 60s WebSocket staleness watchdog rebuilds a dead socket and
  resubscribes (the SDK does not auto-reconnect).

## 3. Strategies (see `../STRATEGY_LOG.md` for results)
* **obi_v1–v4** — Order-Book-Imbalance scalping variants (maker / taker / hybrid /
  let-winners-run). All net-negative on closed trades; retained for comparison.
* **carry_v1** — delta-neutral funding carry. Current **core** ("set-and-forget").

## 4. Evaluation model
* **Paper-on-mainnet:** real order book / trades / funding, simulated fills, zero
  on-chain risk. The journal + dashboard analytics are the verdict.
* A/B by `STRATEGY_NAME` tag; the dashboard compares per-strategy expectancy.

## 5. Deviations from original design
* **Margin:** Unified (not isolated-only) — the account couldn't switch off Unified;
  the bot reads balances correctly and sets per-position isolation within the pool.
* **Capital:** $250/strategy (paper) and $1,000 (carry), not $50.
* **Added:** full paper engine + SQLite journal, carry allocator, multi-page
  dashboard, monitor/botctl tooling, WS auto-reconnect.
* **Emergency flatten-on-disconnect** replaced by the reconnect watchdog (flattening
  on every transient blip would churn costs; positions carry a protective bracket).
