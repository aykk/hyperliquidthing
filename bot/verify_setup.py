"""One-off setup verification (READ-ONLY).

Checks:
  1. The agent private key actually derives AGENT_ADDRESS.
  2. The agent is currently authorized to trade for MAIN_ACCOUNT_ADDRESS.

Run: ./.venv/bin/python verify_setup.py
"""

from __future__ import annotations

from eth_account import Account
from hyperliquid.info import Info

from config import AGENT_ADDRESS, API_URL, MAIN_ACCOUNT_ADDRESS, WALLET_PRIVATE_KEY


def check_keypair() -> bool:
    if not WALLET_PRIVATE_KEY:
        print("[FAIL] WALLET_PRIVATE_KEY is missing from .env")
        return False
    derived = Account.from_key(WALLET_PRIVATE_KEY).address
    expected = (AGENT_ADDRESS or "").strip()
    match = derived.lower() == expected.lower()
    print(f"  derived agent address : {derived}")
    print(f"  expected AGENT_ADDRESS : {expected or '(unset)'}")
    print(f"  [{'OK' if match else 'FAIL'}] keypair {'matches' if match else 'does NOT match'}")
    return match


def check_authorization() -> bool:
    if not MAIN_ACCOUNT_ADDRESS:
        print("[FAIL] MAIN_ACCOUNT_ADDRESS is missing from .env")
        return False
    info = Info(API_URL, skip_ws=True)
    agents = info.extra_agents(MAIN_ACCOUNT_ADDRESS) or []
    target = (AGENT_ADDRESS or "").strip().lower()
    print(f"  authorized agents for master: {agents}")
    for agent in agents:
        if agent.get("address", "").lower() == target:
            print(f"  [OK] agent is authorized (valid until {agent.get('validUntil')})")
            return True
    print("  [FAIL] agent is NOT authorized for the master account")
    return False


def main() -> None:
    print("== 1. Keypair check ==")
    keypair_ok = check_keypair()
    print("\n== 2. On-chain authorization check ==")
    auth_ok = check_authorization()
    print("\n== Summary ==")
    print(f"  keypair match     : {'PASS' if keypair_ok else 'FAIL'}")
    print(f"  agent authorized  : {'PASS' if auth_ok else 'FAIL'}")


if __name__ == "__main__":
    main()
