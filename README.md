experimental and educational trading bot for [Hyperliquid](https://hyperliquid.xyz) perpetual futures (perps). made with python + websocket (this stack is way too slow btw) that runs simulated perps trades on live mainnet data from an agent wallet

implemented a sqlite trading journal and threw in a web dashboard

tried 4 different strategies (non-progressive, more info in STRATEGY_LOG.md) and a fifth carry strategy:

all four strategies use the same entry signal from the top of the l2 book, compute dollar volume on each side.

**None of them made any money past a short-term window. Every version was net-negative after fees!**

## How I tested

Hyperliquid has a testnet (fake money), but its order books are too thin to test a signal like this, since there's barely anyone trading. So I used two setups:

- **Live testnet:** real orders sent to the exchange, using fake money. Used for v1 and v2.
- **Paper trading on mainnet data:** the bot watches the real order book and real trades, but only *simulates* its own orders. Used for v3, v4 and the carry strategy.

Paper trading is easy to fool yourself with, because it's easy to assume every order fills at a good price. The simulator tries hard not to:

- Waiting orders fill only when they "realistically" would, so if the bot places an order at a price where $50k is already waiting, it only counts as filled once $50k of real trades have gone through at that price.

- Market orders pay for slippage, so exits check the real order book for the full position size instead of assuming the best possible price.

- Fees and funding are charged. Hyperliquid's base fees (0.015% for waiting orders, 0.045% for orders that cross the spread) and hourly funding payments are included by default.

## The signal

**Order book imbalance (OBI):** add up the dollar value of the top 10 price levels on each side of the order book.

```
bid share = bid $ / (bid $ + ask $)
```

- Bid share ≥ 70% → go long (more buyers waiting than sellers)
- Bid share ≤ 30% → go short
- Anything in between → do nothing

The idea is that when far more money is waiting to buy than to sell, the price should drift up in the next few seconds.

## Results

Net P&L is after fees. Every trade was about $25.

| Version | What changed | Tested on | Closed trades | Win rate | Before fees | Net P&L |
|---|---|---|---|---|---|---|
| obi_v1 | Waiting (limit) orders only | Live testnet | 3 | 33% | −$0.02 | −$0.06 |
| obi_v2 | Market orders only | Live testnet | 3 | 0% | −$0.07 | −$0.10 |
| obi_v3 | Mix of both, plus filters to skip bad conditions | Paper, mainnet data | 5 | 0% | +$0.01 | −$0.11 |
| obi_v4 | No early exit; stop-loss, take-profit and trailing stop only | Paper, mainnet data | 15* | 47% | −$0.27 | −$0.52 |

\*4 of obi_v4's 15 were entry orders that never filled and closed at $0.

note: these are very small samples (26 trades across all four versions). They aren't enough to prove anything statistically. The direction was consistent across all four versions, though, and the reasons below line up with why this kind of strategy is known to be hard.

# Notes from each strategy

- **v1, waiting orders:** I placed orders at the best bid or ask and waited for someone to trade into them, to avoid paying the spread. Only about 5% filled. The ones that did fill usually filled right before the price moved against me (adverse selection).

- **v2, market orders:** every signal turned into a real trade, so I could finally test the signal itself. Each trade paid the spread plus fees, and the bot exited whenever the imbalance flipped, usually before the price had moved enough to cover those costs.

- **v3, mixed orders plus filters:** I tried a waiting order first and switched to a market order if it didn't fill. I also skipped trades when the spread was wide, the market was dead, or recent trades disagreed with the signal. It was the only version that came out ahead before fees, by one cent. Regardless, fees tore the strategy down in the end.

- **v4, letting winners run:** I removed the early exit so trades could run until they hit a stop-loss, take-profit or trailing stop. It won about half its trades, but the trailing stop locked in small gains (about +$0.09 each), while each full stop-loss cost about −$0.29. In a choppy market, prices rarely moved far enough in one direction to make up for that.

## Funding carry (`carry_v1`)

Perps have a *funding rate*: a payment every hour between long and short traders that keeps the perp price close to the real asset's price. When funding is positive, shorts get paid.

The carry strategy shorts the perp and buys the same amount of the asset on the spot market. Price moves on the two sides cancel out, so the position doesn't care which way the market goes. It just collects the funding payments.

## Running it

```bash
cd bot
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp ../.env.example ../.env   # fill in your wallet addresses and agent key
```

`.env.example` starts on **testnet** (`IS_MAINNET=false`). Don't put a key for a wallet holding real money in `.env` until you've read `bot/README.md`.

```bash
# Paper-trade the OBI strategy against live mainnet data
IS_MAINNET=true ./.venv/bin/python paper.py

# Scan for the best funding carry opportunities right now (read-only)
IS_MAINNET=true ./.venv/bin/python carry.py scan --top 15

# Paper-trade the carry strategy with $1,000
IS_MAINNET=true CARRY_CAPITAL=1000 ./.venv/bin/python carry.py paper

# Dashboard (http://localhost:3000)
cd ../dashboard && npm install && npm run dev
```

`bot/README.md` has the full module breakdown, every setting, and how the fill simulator works.

## Status

- Refine and run carry strategy properly, then with a small amount of real money. Testing it thoroughly would require running it 24/7 on a server for weeks.

- Try a slower, trend-following strategy that holds for hours or days instead of seconds, where fees matter much less.

- Considering a rebuild of bot in rust as there may be an execution speed gap(?) and renting an AWS server in Tokyo for colocation with Hyperliquid's servers.
