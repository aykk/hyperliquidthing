"""Central configuration for the Hyperliquid bot.

Loads secrets from the repo-root .env. The screener is READ-ONLY and never
touches the private key; that is exposed here only for the execution engine.
"""

import os
from pathlib import Path

import certifi
from dotenv import load_dotenv
from hyperliquid.utils import constants

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

# Ensure the SSL layer (used by the websocket-client WS connection) can find a CA
# bundle. requests already uses certifi, but websocket-client falls back to the
# system store, which is unreliable on macOS. Harmless on Linux/AWS.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("SSL_CERT_DIR", os.path.dirname(certifi.where()))

IS_MAINNET: bool = os.getenv("IS_MAINNET", "true").strip().lower() == "true"

MAIN_ACCOUNT_ADDRESS: str | None = os.getenv("MAIN_ACCOUNT_ADDRESS")
AGENT_ADDRESS: str | None = os.getenv("AGENT_ADDRESS")
WALLET_PRIVATE_KEY: str | None = os.getenv("WALLET_PRIVATE_KEY")


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, falling back to `default` if unset or
    unparseable."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    """Read an int from the environment, falling back to `default` if unset or
    unparseable."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


# --- Risk / sizing (env-configurable per strategy budget) --------------------
# Sized for a $250 per-strategy account by default (testnet wallet holds ~1000
# USDC; we test up to 4 strategies at $250 each).
#
# STARTING_EQUITY     simulated paper account size.
# ORDER_NOTIONAL_USD  notional per trade (protocol minimum is $10).
# MAX_CONCURRENT_POSITIONS  caps total exposure (notional * positions).
STARTING_EQUITY: float = _env_float("STARTING_EQUITY", 250.0)
ORDER_NOTIONAL_USD: float = max(10.0, _env_float("ORDER_NOTIONAL_USD", 25.0))
MAX_CONCURRENT_POSITIONS: int = max(1, _env_int("MAX_CONCURRENT_POSITIONS", 5))
# API write budget (entries + SL + TP + cancels each count once). A trade costs
# ~3 writes, so keep this comfortably above 3x the trades/hour you expect.
MAX_ORDERS_PER_HOUR: int = max(3, _env_int("MAX_ORDERS_PER_HOUR", 30))

# Label every trade with the strategy/version that produced it, so multiple
# strategy experiments can be compared in the journal/dashboard. Set per run,
# e.g. STRATEGY_NAME=obi_v2 ./.venv/bin/python paper.py
STRATEGY_NAME: str = os.getenv("STRATEGY_NAME", "obi_v1").strip() or "obi_v1"

# Entry execution model:
#   "maker" (obi_v1) — rest a post-only (Alo) order at the passive side. Earns
#                      the spread / no taker fee, but fills rarely (~5%) and is
#                      prone to adverse selection.
#   "taker" (obi_v2) — cross the spread with a marketable IOC order. Fills almost
#                      always, so the imbalance signal is actually tested, at the
#                      cost of paying the spread + taker fee each entry.
#   "hybrid" (obi_v3) — rest a post-only order first, then cross as a taker if it
#                      is still unfilled after HYBRID_CHASE_SECONDS. Captures maker
#                      economics when possible, guarantees a fill otherwise.
ENTRY_MODE: str = os.getenv("ENTRY_MODE", "maker").strip().lower()
# For taker entries, cap how far past the touch we are willing to pay/sell, so a
# thin book can't fill us at a runaway price. 0.001 = 0.1%.
MAX_TAKER_SLIPPAGE_PCT: float = _env_float("MAX_TAKER_SLIPPAGE_PCT", 0.001)
# Hybrid only: how long the post-only order rests before we cross as a taker.
# Keep below ORDER_TIF_SECONDS so the chase fires before the stale-cancel sweep.
HYBRID_CHASE_SECONDS: float = _env_float("HYBRID_CHASE_SECONDS", 5.0)

# === obi_v3 signal / risk tuning ============================================
# All gated so the defaults reproduce obi_v1/obi_v2 behavior exactly.
#
# --- Entry signal thresholds (override strategy.py defaults) ----------------
# Imbalance needed to fire. Wider (0.75/0.25) = fewer, higher-conviction trades.
LONG_THRESHOLD: float = _env_float("LONG_THRESHOLD", 0.70)
SHORT_THRESHOLD: float = _env_float("SHORT_THRESHOLD", 0.30)
#
# --- Exit hysteresis + minimum hold (fixes signal-flip churn on noise) ------
# A long exits on signal-flip only when bid_ratio falls BELOW EXIT_LONG_BELOW;
# a short exits only when it rises ABOVE EXIT_SHORT_ABOVE. Defaults = 0.50/0.50
# reproduce the original symmetric flip. Set e.g. 0.45/0.55 to add a dead-band
# so the position is not cut the instant the ratio wiggles past neutral.
EXIT_LONG_BELOW: float = _env_float("EXIT_LONG_BELOW", 0.50)
EXIT_SHORT_ABOVE: float = _env_float("EXIT_SHORT_ABOVE", 0.50)
# Block the signal-flip exit until a position has been held this many seconds,
# so winners have room to clear transaction costs. 0 = no minimum (v1/v2).
# (The protective SL/TP bracket is NEVER blocked by this.)
MIN_HOLD_SECONDS: float = _env_float("MIN_HOLD_SECONDS", 0.0)
#
# --- Entry spread gate ------------------------------------------------------
# Skip entries when the top-of-book spread (in PERCENT, e.g. 0.04 = 0.04%) is
# wider than this, since a wide spread means the round-trip cost eats the edge.
# inf = no gate (v1/v2).
MAX_ENTRY_SPREAD_PCT: float = _env_float("MAX_ENTRY_SPREAD_PCT", float("inf"))
#
# --- Aggressor-flow confirmation + liquidity floor (uses the trades stream) -
# Resting book depth is easily spoofed; confirming with realized aggressor flow
# filters adverse-selected entries. These need REAL trade flow, so they are most
# useful on MAINNET (testnet has little/no flow) — keep them off on testnet.
FLOW_WINDOW_SECONDS: float = _env_float("FLOW_WINDOW_SECONDS", 30.0)
# Require recent aggressor flow to agree with the trade direction before entering.
REQUIRE_FLOW_CONFIRM: bool = os.getenv("REQUIRE_FLOW_CONFIRM", "false").strip().lower() == "true"
# Min fraction of windowed flow on the agreeing side to count as confirmation
# (0.50 = no edge required; 0.55 = mild; 0.60 = strong).
FLOW_CONFIRM_RATIO: float = _env_float("FLOW_CONFIRM_RATIO", 0.55)
# Liquidity/activity floor: require at least this much aggressor $notional in the
# window before entering (skips dead, illiquid, whippy periods). 0 = no floor.
MIN_FLOW_NOTIONAL_USD: float = _env_float("MIN_FLOW_NOTIONAL_USD", 0.0)

# === obi_v4 "let winners run" tuning ========================================
# v1/v2/v3 churned out of every position the instant the imbalance wiggled,
# capturing sub-cost moves. v4's thesis: stop scalping, let a winner run far
# enough to dwarf the round-trip fee, and protect it with a trailing stop. All
# gated so the defaults reproduce earlier behavior exactly.
#
# Hard protective bracket (now env-tunable; were fixed constants before).
STOP_LOSS_PCT: float = _env_float("STOP_LOSS_PCT", 0.015)     # hard stop from entry
TAKE_PROFIT_PCT: float = _env_float("TAKE_PROFIT_PCT", 0.015)  # profit cap from entry
#
# Disable the order-book signal-flip exit entirely so a position is managed only
# by its SL / TP / trailing stop — i.e. winners are allowed to run instead of
# being cut the moment the (noisy) imbalance reverts. False = v1/v2/v3 behavior.
DISABLE_FLIP_EXIT: bool = os.getenv("DISABLE_FLIP_EXIT", "false").strip().lower() == "true"
#
# Trailing stop. After price has moved TRAIL_ACTIVATE_PCT in our favor (measured
# from entry), arm a stop that rides TRAIL_PCT behind the best favorable price
# reached. This locks in gains while letting a trend extend. TRAIL_PCT = 0 keeps
# the trailing stop OFF (v1/v2/v3). e.g. activate at +0.4%, trail 0.25% behind.
TRAIL_PCT: float = _env_float("TRAIL_PCT", 0.0)
TRAIL_ACTIVATE_PCT: float = _env_float("TRAIL_ACTIVATE_PCT", 0.0)

API_URL: str = constants.MAINNET_API_URL if IS_MAINNET else constants.TESTNET_API_URL
