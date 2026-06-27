# Strategy & Execution Engine

> **Status:** reflects current implementation (2026-06-27). Originally a single
> maker-only OBI scalper; now multiple OBI variants (obi_v1–v4) plus a separate
> delta-neutral carry strategy. All params are env-driven in `bot/config.py`.

## 1. Real-Time Data Ingestion
* Persistent WebSocket to Hyperliquid (`API_URL` = mainnet/testnet per `IS_MAINNET`).
* Subscribe to `l2Book` for each target coin; subscribe to `trades` as well when a
  flow feature is enabled (aggressor-flow confirmation / liquidity floor).
* Targets come from the screener, or a fixed universe via `COINS` (e.g.
  `COINS=BTC,ETH,SOL,DOGE`).
* **Staleness watchdog:** if no WS message arrives for 60s, the socket is rebuilt
  and resubscribed (positions are preserved; not re-adopted).

## 2. Trading Signal Logic (Order Book Imbalance)
From the top-of-book depth, compute dollar volume on each side and the ratio:
```
Bid Ratio = Bid Volume / (Bid Volume + Ask Volume)
```
* **BUY_LONG** when `Bid Ratio >= LONG_THRESHOLD` (default 0.70).
* **SELL_SHORT** when `Bid Ratio <= SHORT_THRESHOLD` (default 0.30).

Optional entry gates (default off → reproduce v1/v2):
* **Spread gate** (`MAX_ENTRY_SPREAD_PCT`): skip when top-of-book spread is too wide.
* **Aggressor-flow confirmation** (`REQUIRE_FLOW_CONFIRM`, `FLOW_CONFIRM_RATIO`):
  require recent realized trade flow to agree with the signal (anti-spoof).
* **Liquidity floor** (`MIN_FLOW_NOTIONAL_USD`): skip dead/illiquid windows.

## 3. Entry Execution Models (`ENTRY_MODE`)
* **maker** (obi_v1): rest a post-only (Alo) order at the passive side. Best
  economics, but fills rarely and is prone to adverse selection.
* **taker** (obi_v2): cross the spread with a marketable IOC, capped by
  `MAX_TAKER_SLIPPAGE_PCT`. Reliable fills, pays spread + taker fee.
* **hybrid** (obi_v3): post-only first, then taker-chase if unfilled after
  `HYBRID_CHASE_SECONDS`.

## 4. Exit Logic
* **Protective bracket (always on):** hard stop at `STOP_LOSS_PCT` and take-profit
  at `TAKE_PROFIT_PCT` from entry.
* **Signal-flip exit (v1–v3):** exit when the imbalance reverts past
  `EXIT_LONG_BELOW` / `EXIT_SHORT_ABOVE`, subject to `MIN_HOLD_SECONDS` hysteresis.
* **"Let winners run" (obi_v4):** `DISABLE_FLIP_EXIT=true` turns off the signal-flip
  exit so a position is managed only by SL / TP / **trailing stop**
  (`TRAIL_ACTIVATE_PCT`, `TRAIL_PCT`).

## 5. Risk & Order Routing (`execution.py`)
Before any order: size at `ORDER_NOTIONAL_USD` (≥ $10); enforce
`MAX_CONCURRENT_POSITIONS` and `MAX_ORDERS_PER_HOUR`; verify account state via REST;
attach the SL/TP bracket on fill confirmation.

## 6. Funding-Carry Strategy (`carry.py`, carry_v1)
A separate, **market-neutral** strategy (no OBI signal). Delta-neutral: funding>0 →
short perp + long equal-notional spot; collect funding.

* **scan** mode: rank live opportunities by net-of-cost APR (carry APR minus the
  ~0.23% round-trip amortized over a hold horizon), with a liquidity floor and
  break-even-days. Read-only.
* **paper** mode: a **set-and-forget allocator** — sizes `CARRY_CAPITAL` across the
  best net-APR, liquid, hedgeable, positive-funding coins (`CARRY_MAX_POSITIONS`,
  `CARRY_PER_COIN_PCT`, `CARRY_MAX_DEPLOY_PCT`, `CARRY_MIN_NET_APR`,
  `CARRY_MIN_VOL_M`); accrues funding hourly; rebalances every
  `CARRY_REBALANCE_SEC` (exit funding-decayed carries, rotate into better ones).
  Journals each carry as `carry_v1`; writes `data/carry_status.json`.
