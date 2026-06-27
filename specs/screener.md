# Asset Screener Module (`screener.py`)

> **Status:** reflects current implementation (2026-06-27). Filter thresholds were
> revised from the original design after validating against live data — the
> original values either never filtered (funding) or excluded every liquid major
> (spread). Updated values below; original noted inline. The screener is optional:
> runs often pin a fixed universe via the `COINS` env var instead.

## 1. Objective
The Screener acts as an independent background service. Its sole purpose is to query the Hyperliquid API, filter the universe of perpetual assets based on mathematical safety thresholds, and output a list of the top high-probability tickers to the Strategy Engine.

## 2. API & Data Ingestion
* **Endpoint:** Use the Hyperliquid Python SDK `Info` class, specifically the `meta_and_asset_ctxs()` method, which fetches both the universe metadata (token names, decimals) and current context (volume, funding, mark price) in one call.
* **Frequency:** The screener should run synchronously every 10 minutes (600 seconds) using a schedule loop.

## 3. Filtering Pipeline (Hard Constraints)
Iterate through the returned asset contexts and apply the following filters sequentially. If an asset fails ANY of these checks, immediately drop it from the list.

### A. Volume Liquidity Filter
* **Condition:** `dayNtlVlm` (24-hour notional volume) must be strictly greater than `$10,000,000` USD.
* **Rationale:** Protects the bot from getting trapped in thin order books where slippage would eat the $50 starting capital.

### B. Funding Rate Filter
* **Condition:** `abs(funding) < 0.0001` (current hourly rate).
  * *(Original spec: `< 0.0020` — a no-op, since the universe's max hourly funding
    is ~0.0004, so the old threshold never filtered anything.)*
* **Rationale:** Avoids assets in a funding blowoff that would erode a held position.

### C. Spread Filter (Liquidity Gate)
* **Condition:** `Spread % = ((askPx - bidPx) / askPx) * 100` must be **tighter than
  `0.08%`** (drop anything wider). `bidPx/askPx` come from the context or
  `impactPxs` as a proxy.
  * *(Original spec required spread **wider** than 0.05%, which excluded every
    liquid major and forced the bot into thin meme books — flipped to a tightness
    gate so a $10 order + its stop don't suffer heavy slippage.)*
* **Rationale:** keep only tight, liquid books.

## 4. Sorting & Output
* Once the universe of 100+ tokens is filtered down to the qualifying assets, sort the remaining assets by **Highest 24-hour Volume**.
* Extract the **Top 2 Tickers** (e.g., `["SOL", "WIF"]`).
* **Return/Output:** The Python function should return a clean list of these 2 string tickers. This list will be ingested by the WebSocket subscription module in the Strategy Engine.

## 5. Development Instructions for Cursor AI
* **Library:** Use the official `hyperliquid-python-sdk` package. 
* **Imports required:** `from hyperliquid.info import Info` and `from hyperliquid.utils import constants`.
* **Error Handling:** Wrap the API call in a `try-except` block. If the API rate limits or drops the connection, wait 5 seconds and retry. Do not crash the entire bot if one poll fails.
* **Logging:** Use Python's built-in `logging` module to print a neat console summary of the top 2 selected assets every time the screener runs.