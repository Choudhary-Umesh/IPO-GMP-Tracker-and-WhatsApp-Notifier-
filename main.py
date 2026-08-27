#!/usr/bin/env python3
"""IPO Tracker & WhatsApp Notifier — pipeline entry point.

    Step 1  InvestorGain  -> filter (closes today AND GMP% > 15) -> SQLite
    Step 2  IPO Watch     -> fuzzy-match those names -> enrich SQLite
    Step 3  Build message -> send over WhatsApp

Usage:
    python main.py                 # full pipeline, sends WhatsApp
    python main.py --dry-run       # runs scrapers, prints the message instead
    python main.py --date 2026-08-20   # pretend "today" is another date
    python main.py --test-message  # skip scraping, just verify WhatsApp works
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

from src import db, investorgain_scraper, ipowatch_scraper, notifier
from src.config import SEND_WHEN_EMPTY
from src.formatter import build_message
from src.utils import now_ist, today_ist


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="IPO Tracker & WhatsApp Notifier")
    p.add_argument("--dry-run", action="store_true", help="print the message, don't send")
    p.add_argument("--date", help="override today's date (YYYY-MM-DD), for testing")
    p.add_argument("--test-message", action="store_true", help="send a WhatsApp test only")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)
    log = logging.getLogger("main")

    if args.test_message:
        notifier.send_whatsapp(
            "✅ IPO Tracker test message — your WhatsApp setup works!",
            dry_run=args.dry_run,
        )
        return 0

    run_date = dt.date.fromisoformat(args.date) if args.date else today_ist()
    log.info("Run started at %s IST (target date: %s)",
             now_ist().strftime("%Y-%m-%d %H:%M:%S"), run_date)

    # ---------------------------------------------------------------- Step 1
    db.init_db(reset=True)
    candidates = investorgain_scraper.run(target_date=run_date)
    log.info("STEP 1 complete: %s candidate(s)", len(candidates))

    # ---------------------------------------------------------------- Step 2
    if candidates:
        ipowatch_scraper.run()
        log.info("STEP 2 complete")

    rows = db.get_candidates()

    # ---------------------------------------------------------------- Step 3
    message = build_message(rows, run_date, run_time=now_ist())
    log.info("Message built (%s chars)", len(message))

    if not rows and not SEND_WHEN_EMPTY and not args.dry_run:
        log.info("No matches and SEND_WHEN_EMPTY is false — nothing sent.")
        return 0

    notifier.send_whatsapp(message, dry_run=args.dry_run)
    log.info("STEP 3 complete: notification dispatched")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("main").exception("Pipeline failed: %s", exc)
        raise SystemExit(1)
