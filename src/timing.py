"""Send-window logic, kept free of I/O so it can be unit-tested.

GitHub's shared cron scheduler gives no timing guarantee. This repo's own run
history showed scheduled runs starting 5-9 hours late (6-10 PM IST for a
12:53 IST cron), which is why alerts never arrived by 2 PM. The workflow
therefore has a precise external trigger plus several early backup crons;
these helpers decide how long a backup run should wait and how late a message is.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from .config import IST


def parse_hhmm(value: str) -> dt.time:
    hours, minutes = value.strip().split(":")
    return dt.time(int(hours), int(minutes))


def at_ist(day: dt.date, hhmm: str) -> dt.datetime:
    return dt.datetime.combine(day, parse_hhmm(hhmm), tzinfo=IST)


def seconds_until(now: dt.datetime, hhmm: str) -> float:
    """Seconds from `now` until HH:MM IST on the same IST day (0 if passed)."""
    now = now.astimezone(IST)
    return max(0.0, (at_ist(now.date(), hhmm) - now).total_seconds())


def wait_plan(now: dt.datetime, not_before: str, max_wait_minutes: int) -> tuple[str, float]:
    """For a backup run: ('go', 0) if it is already time, ('wait', s) to sleep
    s seconds first, or ('skip', s) if the wait would exceed the cap — a later
    backup cron will cover the day instead."""
    secs = seconds_until(now, not_before)
    if secs <= 0:
        return "go", 0.0
    if secs > max_wait_minutes * 60:
        return "skip", secs
    return "wait", secs


def lateness_banner(now: dt.datetime, deadline: str, cutoff: str) -> Optional[str]:
    """A warning line when the alert goes out after the deadline, else None."""
    now = now.astimezone(IST)
    if now <= at_ist(now.date(), deadline):
        return None
    stamp = now.strftime("%H:%M")
    if now > at_ist(now.date(), cutoff):
        return (f"⚠️ LATE ALERT ({stamp} IST) — the scheduler was delayed; "
                "subscription has most likely already closed.")
    return f"⚠️ Delayed alert ({stamp} IST, target was {deadline}) — apply soon."
