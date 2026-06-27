"""Bot control from the terminal — independent of the dashboard.

The live/paper engine runs as its own detached process, so it KEEPS RUNNING even
if you quit the Next app. Use this to check on it or stop it without the UI.

Usage:
  ./.venv/bin/python botctl.py status
  ./.venv/bin/python botctl.py stop
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

STATUS_PATH = Path(__file__).resolve().parent / "data" / "bot_status.json"


def _read() -> dict | None:
    try:
        return json.loads(STATUS_PATH.read_text())
    except Exception:  # noqa: BLE001
        return None


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def status() -> int:
    s = _read()
    if not s:
        print("no status file — bot has never run (or data/ was cleared)")
        return 0
    pid = s.get("pid")
    alive = _alive(pid)
    running = alive and s.get("state") != "stopped"
    print(f"state file : {s.get('state')}  (mode={s.get('mode')}, network={s.get('network','?')})")
    print(f"pid {pid}   : {'ALIVE' if alive else 'not running'}")
    print(f"=> bot is  : {'RUNNING' if running else 'STOPPED'}")
    if running:
        print(f"   strategy={s.get('strategy')} equity={s.get('equity')} "
              f"open={s.get('open_positions')} coins={s.get('coins')}")
        print(f"   started {s.get('started_at')}  last heartbeat {s.get('updated_at')}")
    return 0


def stop() -> int:
    s = _read()
    pid = s.get("pid") if s else None
    if not _alive(pid):
        print("bot is not running; nothing to stop")
        return 0
    print(f"sending SIGTERM to pid {pid} (graceful shutdown) ...")
    os.kill(pid, signal.SIGTERM)
    for _ in range(10):
        time.sleep(0.5)
        if not _alive(pid):
            print("stopped.")
            return 0
    print("still alive after 5s; check manually (kill -9 as last resort).")
    return 1


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        return status()
    if cmd == "stop":
        return stop()
    print(f"unknown command '{cmd}'; use 'status' or 'stop'", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
