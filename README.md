experimental bot with python + websocket (this stack is way too slow btw) that runs simulated perps trades on live mainnet data from an agent wallet
implemented a sqlite trading journal and threw in a vibecoded next.js dashboard

tried 4 different strategies (more info in STRATEGY_LOG.md and the spec files) and a fifth carry strategy:

all four strategies use the same entry signal from the top of the l2 book, compute dollar volume on each side:

## obi_v1: maker-only scalp

thesis: rest on the passive side of the book and let someone else cross the spread to you. if the obi signal is real, you get a better price than taker and pay maker fees instead of taker fees.

entry: ENTRY_MODE=maker. place a post-only (alo) limit at the best bid (long) or best ask (short). the order sits in the fifo queue until filled or cancelled after the time-in-force window.

exit: standard bracket, stop-loss and take-profit at 1.5% from entry. also exit when the obi signal flips (bid ratio drops below the exit threshold on a long, etc.).

result (live testnet): 3 closed · 67% win rate · net −$0.021 · −$0.007/trade.

what i learned: maker fills were rare (~5% of signals). when they did fill, they were adversely selected, price tended to move against us immediately after fill. rare fills + toxic fills = no edge. this is the same failure mode that makes homemade market-making non-viable at our latency tier.

## obi_v2: taker-only scalp

thesis: obi_v1 never actually tested the signal because almost nothing filled. cross the spread aggressively so every signal becomes a real position and we can measure whether obi predicts short-term direction.

entry: ENTRY_MODE=taker. send a marketable ioc order that lifts the ask (long) or hits the bid (short). slippage capped by MAX_TAKER_SLIPPAGE_PCT.

exit: same as v1, stop-loss, take-profit, and signal-flip exit. positions close quickly when the imbalance reverts.

result (live testnet): 3 closed · 0% win rate · net −$0.068 · −$0.023/trade.

what i learned: fills happen reliably now, but every entry pays the full spread plus taker fee (~0.045%). signal-flip exits close positions on tiny moves that never recover those costs. the gross signal is roughly coin-flip; costs turn it net-negative.

## obi_v3: hybrid entry with confirmation gates

thesis: combine v1's maker economics when possible with v2's fill reliability as a fallback, and only trade when extra conditions confirm the signal isn't noise or spoofing.

entry: ENTRY_MODE=hybrid. post-only limit first; if unfilled after HYBRID_CHASE_SECONDS, chase with a taker ioc. additional gates (all configurable, off in v1/v2):

spread gate, skip when top-of-book spread is too wide
aggressor-flow confirmation, recent trade flow must agree with the obi direction
liquidity floor, skip dead/illiquid windows
exit: signal-flip exit with hysteresis (MIN_HOLD_SECONDS) so we don't churn in and out on flickering signals. still uses stop-loss and take-profit brackets.

result (paper mainnet): 5 closed · 0% win rate · net −$0.106 · −$0.021/trade.

what i learned: smarter entries and filters reduced bad trades but didn't create positive expectancy. positions still exited on signal flips before moves cleared round-trip costs. the core problem wasn't entry quality, it was that top-of-book obi doesn't predict enough at this timescale to beat fees.

## obi_v4: let winners run

thesis: v1–v3 all exit too early via signal-flip, scalping out of positions before a real move can pay for costs. disable the flip exit and let stop-loss, take-profit, and a trailing stop manage the trade so winners can run far enough to dwarf fees.

entry: same hybrid/taker/maker modes as before (ran with default hybrid settings in paper). no new entry gates beyond v3.

exit: DISABLE_FLIP_EXIT=true. no signal-flip exit. position lives until one of:

hard stop-loss at 1% (STOP_LOSS_PCT=0.01)
take-profit at 1.5% (TAKE_PROFIT_PCT=0.015)
trailing stop activates after +0.4% unrealized, trails at 0.25% (TRAIL_ACTIVATE_PCT, TRAIL_PCT)
result (paper mainnet): 13 closed · 38% win rate · net −$0.616 · −$0.047/trade.

what i learned: worst per-trade expectancy of all four variants. in a flat, low-microstructure-vol regime there is no trend to "run", positions mostly hit the wider stop. the strategy is direction-symmetric, so it opens longs into a downtrend as often as shorts, fighting the tape. open positions often showed green unrealized pnl (misleading); closed-trade expectancy was clearly negative.

summary: all four obi variants were net-negative on closed trades. top-of-book imbalance scalping had no edge for a non-colocated participant at this capital size. see STRATEGY_LOG.md for full numbers and regime context.

a fifth strategy, carry_v1 (funding carry), was added later as the pivot: delta-neutral short-perp + long-spot to collect funding payments. market-neutral, doesn't need volatility. see STRATEGY_LOG.md for full history and numbers.

hyperliquid testnet books are too thin to test a scalping signal meaningfully. so we used paper-on-mainnet:

carry paper can optionally read mainnet funding rates while your wallet stays on testnet (CARRY_FUNDING_MAINNET=true), because testnet funding data is not useful for opportunity scanning.

at the end of the day ts bricked so i'm gonna try making something with rust and renting an aws server in tokyo to run the bot on so i have the advantage of colocation
