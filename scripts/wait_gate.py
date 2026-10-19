#!/usr/bin/env python3
"""Backup-run gate for GitHub Actions (standard library only, no pip install).

    python3 scripts/wait_gate.py 13:40 210

Sleeps until 13:40 IST if that is at most 210 minutes away, then writes
`proceed=true` to $GITHUB_OUTPUT. If the wait would be longer, writes
`proceed=false` and exits immediately — a later backup cron covers the day.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import IST  # noqa: E402  (config has no third-party imports)
from src.timing import wait_plan  # noqa: E402


def main(argv: list[str]) -> int:
    not_before = argv[1] if len(argv) > 1 else "13:40"
    max_wait = int(argv[2]) if len(argv) > 2 else 210
    now = dt.datetime.now(IST)
    decision, secs = wait_plan(now, not_before, max_wait)
    print(f"{now:%F %T} IST · send window opens {not_before} IST · "
          f"decision: {decision} ({secs / 60:.0f} min, cap {max_wait} min)", flush=True)
    if decision == "wait":
        time.sleep(secs)
        print(f"Woke at {dt.datetime.now(IST):%T} IST", flush=True)
    out = os.getenv("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"proceed={'false' if decision == 'skip' else 'true'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
