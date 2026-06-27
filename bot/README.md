# Hyperliquid Bot — Python Backend

Decoupled trading bot modules. Per `specs/spec.md`, each layer is independent.

## Modules
- `screener.py` — **(done)** Read-only asset discovery. Filters the perp universe by
  volume / funding / spread and emits the top-2 tickers for the strategy engine.
- `strategy.py` — **(done)** Real-time L2 WebSocket ingestion + order-book-imbalance
  signals (`BUY_LONG` / `SELL_SHORT` / `NEUTRAL`) plus `should_exit()` signal-flip logic.
- `execution.py` — **(done, DRY-RUN by default)** Risk gate + isolated post-only order
  routing, stop-loss / take-profit, 15s stale-order cancel, and emergency flatten.
- `paper.py` — **(done)** Paper-trading engine: runs the real strategy against LIVE
  mainnet order books, simulates conservative maker fills + TP/SL/flip exits, and
  records every trade to the journal. Zero risk; used to measure signal edge.
- `journal.py` — **(done)** SQLite trade journal. Logs entry features (bid_ratio,
  spread, funding) + outcomes, computes stats, and exports CSV/JSON.
- `config.py` — Loads secrets from the repo-root `.env` and resolves the API URL.
- `verify_setup.py` — One-off check that the agent keypair matches `AGENT_ADDRESS`
  and that the agent is authorized for `MAIN_ACCOUNT_ADDRESS` (read-only).

## Execution safety
`execution.py` defaults to `dry_run=True`: it reads real state and logs every order
it *would* send, but submits nothing. Risk checks (run against LIVE state, never local):
isolated 1x margin, exactly one $10 position per asset, max 5 concurrent positions,
max 5 orders/hour, and a free-margin check. Only set `dry_run=False` after testing on
testnet (`IS_MAINNET=false`) and funding the account.

### Account abstraction (unified account)
This account is in **`unifiedAccount`** mode: spot + perps share one USDC balance, so
the perps `clearinghouseState` reports $0 (not meaningful). The engine detects the mode
via `userAbstraction` and reads available margin from `spotClearinghouseState` instead.
Verify per-position isolated margin + position reporting under unified mode with
`verify_testnet.py` (see **Testnet validation** below).

## Testnet validation
`verify_testnet.py` validates the two open questions from the unified-account
reconciliation: (1) that `update_leverage(..., is_cross=False)` produces a genuinely
*isolated* position inside the unified pool, and (2) that the position reports back
correctly via `user_state` (`szi`, `leverage.type == "isolated"`, `marginUsed`).

It is **safe by default** (read-only) and **hard-refuses to `--place` on mainnet**.

One-time testnet setup:
1. In `.env`, set `IS_MAINNET=false`.
2. Create/approve a **testnet** agent wallet for your testnet master account at
   <https://app.hyperliquid-testnet.xyz> (API → agent wallets) and put that agent's
   key in `WALLET_PRIVATE_KEY` / its address in `AGENT_ADDRESS`. Set
   `MAIN_ACCOUNT_ADDRESS` to your testnet master address.
3. Fund the testnet master with mock USDC from the testnet faucet (Hyperliquid
   testnet UI → "Faucet"; or the `drip` endpoint).

```bash
# Read-only: connectivity, agent auth, account mode, free margin (no orders)
./.venv/bin/python verify_testnet.py

# Round-trip (TESTNET ONLY): set ETH 1x isolated, open ~$12, confirm the position
# is reported isolated, then market-close and confirm flat.
./.venv/bin/python verify_testnet.py --place --coin ETH --notional 12
```

## Setup
```bash
cd bot
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Secrets are read from the repo-root `.env` (see `../.env.example`).

## Run
```bash
# Verify keypair + agent authorization (read-only)
./.venv/bin/python verify_setup.py

# Run the screener loop (polls every 10 minutes)
./.venv/bin/python screener.py

# Run the strategy engine (prints live signals)
./.venv/bin/python strategy.py

# Run PAPER trading vs live data (records to data/trades.db). Ctrl-C prints stats.
./.venv/bin/python paper.py
```

### Exporting trades for analysis
```python
from journal import TradeJournal
j = TradeJournal()
j.export_csv("export.csv")    # one row per trade, all features + outcomes
j.export_json("export.json")  # trades + aggregate stats
print(j.stats())
```
The CSV is designed to be handed to an LLM: each row pairs the entry context
(bid_ratio, spread, funding, asset, side) with the outcome (pnl, return %, exit
reason), so patterns like "longs win when funding is low" become visible.

### Dashboard analytics & bot control
The dashboard (`../dashboard`) reads this same `data/trades.db` directly. The
journal renders as live analytics across the pages — net pnl, win rate, expectancy,
max drawdown, an equity curve (on **live portfolio**), exit-reason / per-coin /
per-strategy breakdowns, and a filterable/sortable trade table with CSV/JSON export
(on **trades**). It polls `/api/paper` (a Next route that opens the SQLite file
read-only with WAL) every few seconds, so the journal updates whether or not the
bot is running — **you never need the bot running just to view trades/pnl.**

**Start/stop control.** The bot and dashboard are decoupled processes; the journal
file is normally the only shared surface. As a local convenience, the live-portfolio
page has a **paper engine** control: it `GET`s `/api/bot` for status and `POST`s
`start`/`stop`. Start spawns `./.venv/bin/python paper.py` (logging to
`data/paper.log`); stop sends `SIGTERM`, which `paper.py` handles for a graceful
shutdown (unsubscribe, flush stats, write `stopped`). `paper.py` writes a heartbeat
to `data/bot_status.json` (~every 2s) with state, pid, equity, open positions and
coins, which drives the status pill. This control only works when the dashboard runs
on the same machine as the bot (local dev); it is intentionally omitted from any
remote deploy.

## Live runner (`live.py`) — real orders

`paper.py` simulates; **`live.py` places real orders** on the venue selected by
`IS_MAINNET` (testnet by default). It wires the pieces into one loop:

- **l2Book stream → entries.** Computes the order-book-imbalance signal; when flat
  and actionable and risk-ok, places a post-only maker entry (anti-spam guard +
  TIF cancel for unfilled entries).
- **l2Book stream → flip exit.** While holding, if the imbalance reverts through the
  neutral band, it market-closes (`exit_reason="signal_flip"`).
- **userFills stream → journaling + protection.** On an OPEN fill it journals the
  entry (`mode="live"`, tagged with `STRATEGY_NAME`) and places reduce-only SL/TP.
  On a CLOSE fill it journals the exit with the real `closedPnl` and cancels the
  sibling protective order.
- **Heartbeat.** Writes `data/bot_status.json` (state, equity from chain, open
  positions, coins) so the dashboard control shows status and can stop it.

Coin universe: `COINS` env wins; else the screener on mainnet; else `BTC,ETH` on
testnet (the volume screener finds nothing on testnet).

```bash
# Validate wiring first — logs intended orders, sends nothing:
LIVE_DRY_RUN=true ./.venv/bin/python live.py
# Go live (real testnet orders):
./.venv/bin/python live.py
```

The dashboard **testnet bot** start/stop control runs this (`live.py`, real orders).
Fund the PERPS account first (`transfer.py spot-to-perp`) or it exits with
insufficient margin.

### Wallet transfers (`transfer.py`)
Deposits land in **spot**, but `default`-mode perps trade off the **perps** balance:
```bash
./.venv/bin/python transfer.py balances
./.venv/bin/python transfer.py spot-to-perp 998   # make funds tradable
./.venv/bin/python transfer.py perp-to-spot 100   # reverse
./.venv/bin/python transfer.py send 50 0xDEST      # send spot USDC to another wallet
```

### Strategy tagging
Every trade is stamped with `STRATEGY_NAME` (env, default `obi_v1`). Bump it per
experiment (`STRATEGY_NAME=obi_v2 ./.venv/bin/python paper.py`) to compare strategy
versions side-by-side in the dashboard's strategy filter and the "pnl by strategy"
breakdown. The journal auto-migrates older DBs (adds the `strategy` column).

## Fill-model caveat (paper engine)
The paper engine subscribes to BOTH `l2Book` and `trades` for every coin and
models fills against real order flow:

**Queue-based maker entries.** A post-only entry rests at the passive side (best
bid for a long / best ask for a short). At placement we record `queue_ahead` =
the resting size already sitting at our price level (the FIFO queue we join
behind). We then watch the live `trades` stream: every aggressor trade that
executes *at our level on our side* burns down that queue —

- long resting at `P`: aggressive **sells** (`side="A"`) at `px <= P`
- short resting at `P`: aggressive **buys** (`side="B"`) at `px >= P`

Once `queue_ahead <= 0` we are filled at `P`. As a fallback we also fill if the
book trades clean through our level (long: `best_ask <= P`; short:
`best_bid >= P`). Unfilled after the 15s TIF, the order cancels. This models real
maker fill *probability* and adverse selection: orders fill only when enough real
volume trades through the queue ahead of them, not the instant price merely
touches the level.

**Depth-aware taker exits.** Take-profit is a maker limit at the TP price (no
slippage). Stop-loss and signal-flip are taker market exits: instead of assuming
a fill at the trigger/touch price, we walk the live L2 book on the opposite side
for our full position size and use the size-weighted average price (a long sells
into the bids; a short buys from the asks). Slippage against the trigger is
therefore captured, including the case where visible depth is thin.

In live testing many signals never fill (maker adverse selection) — this is
realistic and intentional, not a bug.

**Funding accrual.** Positions held across an hourly funding stamp are debited
(longs, when the rate is positive) or credited (shorts) using the coin's current
rate from `funding_map`. This is an approximation — it applies the latest known
rate to entry notional rather than reconstructing the exact per-hour rate path.

**Trades side convention.** Verified empirically against the live BTC book:
`side="B"` is an aggressive BUY (lifts the ask) and `side="A"` is an aggressive
SELL (hits the bid). See `_aggressor_is_buy` in `paper.py`.

**Config.** Starting equity is configurable via `STARTING_EQUITY` in `.env`
(default `250.0`); per-trade notional is `ORDER_NOTIONAL_USD` (default `25`, min `10`).

`test_paper_sim.py` contains deterministic synthetic tests (no network) covering
queue burn-down + fill, taker-exit slippage, the TIF cancel, and funding accrual:
```bash
./.venv/bin/python test_paper_sim.py
```

## Filter thresholds (tuned — differs from `specs/screener.md`)
| Filter | Original spec | Tuned value | Why changed |
|--------|---------------|-------------|-------------|
| Volume | `dayNtlVlm` > $10M | unchanged | OK |
| Funding | `abs(funding)` < 0.0020 | `< 0.0001` | 0.0020 was a no-op — live max hourly funding is ~0.0004, so 230/230 assets passed |
| Spread | spread % > 0.05% (wider) | spread % < 0.08% (tighter) | Original excluded all liquid majors and forced thin meme books; flipped to a liquidity gate |

Result of the tuning: top-2 output moved from illiquid mid-caps (`XPL`, `NEAR`)
to liquid majors (`BTC`, `ETH`), which are far safer for a $10 order, its 1.5%
stop, and emergency market-flatten.

### Note on the spread filter
`meta_and_asset_ctxs()` does **not** return explicit `bidPx`/`askPx`. The screener
uses `impactPxs` (`[impactBid, impactAsk]`) as the closest available bid/ask proxy.

## Strategy exits (fix)
The original strategy defined no profitable exit (only a stop-loss). The execution
engine should close a position on the FIRST of:
1. **Take-profit** — price reaches `TAKE_PROFIT_PCT` (+1.5%) via a maker limit exit.
2. **Signal-flip** — `strategy.should_exit()` true once the imbalance reverts through
   the neutral band (the entry edge is gone).
3. **Stop-loss** — the 1.5% hard stop fires.
