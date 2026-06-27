"""Wallet transfer helper for the bot wallet (testnet/mainnet per .env).

In `default` (standard) account mode, perps trade off the PERPS balance, but
deposits/sends land in SPOT. Use this to move USDC spot<->perps, or to send spot
USDC back to another address (e.g. your personal wallet).

Usage:
  ./.venv/bin/python transfer.py balances
  ./.venv/bin/python transfer.py spot-to-perp 998
  ./.venv/bin/python transfer.py perp-to-spot 100
  ./.venv/bin/python transfer.py send 50 0xYOURADDRESS   # sends SPOT USDC out

All actions print the before/after balances. On mainnet they move REAL funds;
the script refuses unless you pass --yes.
"""

from __future__ import annotations

import argparse
import sys

import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

from config import API_URL, IS_MAINNET, MAIN_ACCOUNT_ADDRESS, WALLET_PRIVATE_KEY

USDC_SPOT_TOKEN = "USDC:0"  # name:tokenIndex for spot USDC


def _info() -> Info:
    return Info(API_URL, skip_ws=True)


def _balances(info: Info, addr: str) -> tuple[float, float]:
    perps = info.user_state(addr)
    spot = info.spot_user_state(addr)
    perp_usdc = float(perps.get("withdrawable", 0) or 0)
    spot_usdc = 0.0
    for b in spot.get("balances", []):
        if b.get("coin") == "USDC":
            spot_usdc = float(b.get("total", 0) or 0)
    return spot_usdc, perp_usdc


def _print_balances(info: Info, addr: str, label: str) -> None:
    spot_usdc, perp_usdc = _balances(info, addr)
    print(f"  [{label}] spot USDC = {spot_usdc:.4f} | perps withdrawable = {perp_usdc:.4f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bot wallet transfer helper")
    parser.add_argument(
        "action",
        choices=["balances", "spot-to-perp", "perp-to-spot", "send"],
    )
    parser.add_argument("amount", nargs="?", type=float, help="USDC amount")
    parser.add_argument("destination", nargs="?", help="destination address (send only)")
    parser.add_argument("--yes", action="store_true", help="required to move funds on mainnet")
    args = parser.parse_args()

    if not WALLET_PRIVATE_KEY or not MAIN_ACCOUNT_ADDRESS:
        print("WALLET_PRIVATE_KEY and MAIN_ACCOUNT_ADDRESS must be set in .env", file=sys.stderr)
        return 2

    net = "MAINNET" if IS_MAINNET else "testnet"
    addr = MAIN_ACCOUNT_ADDRESS
    info = _info()

    print(f"Wallet {addr} on {net}")
    _print_balances(info, addr, "before")

    if args.action == "balances":
        return 0

    if args.amount is None or args.amount <= 0:
        print("amount must be a positive number", file=sys.stderr)
        return 2

    if IS_MAINNET and not args.yes:
        print("Refusing to move REAL funds on mainnet without --yes", file=sys.stderr)
        return 2

    agent = eth_account.Account.from_key(WALLET_PRIVATE_KEY)
    exchange = Exchange(agent, API_URL, account_address=MAIN_ACCOUNT_ADDRESS)

    if args.action == "spot-to-perp":
        print(f"Moving {args.amount} USDC spot -> perps ...")
        res = exchange.usd_class_transfer(args.amount, True)
    elif args.action == "perp-to-spot":
        print(f"Moving {args.amount} USDC perps -> spot ...")
        res = exchange.usd_class_transfer(args.amount, False)
    else:  # send
        if not args.destination:
            print("send requires a destination address", file=sys.stderr)
            return 2
        print(f"Sending {args.amount} spot USDC -> {args.destination} ...")
        res = exchange.spot_transfer(args.amount, args.destination, USDC_SPOT_TOKEN)

    print("result:", res)
    # Balances settle a moment after the action is acknowledged.
    import time

    time.sleep(2)
    _print_balances(info, addr, "after")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
