#!/usr/bin/env python3
"""IPO Tracker & WhatsApp Notifier — pipeline entry point.

    Step 1  InvestorGain  -> filter (closes today AND GMP% > threshold) -> SQLite
    Step 2  IPO Watch     -> fuzzy-match those names -> enrich SQLite (optional)
    Step 3  Build message -> send over WhatsApp (fallback channel if that fails)

Nothing fails silently: if a data source breaks you still get a message saying
so, and if delivery breaks the run exits non-zero (red in GitHub Actions) after
trying the fallback channel.

Usage:
    python main.py                      # full pipeline, sends the alert
    python main.py --dry-run            # runs scrapers, prints the message instead
    python main.py --date 2026-08-20    # pretend "today" is another date
    python main.py --test-message       # skip scraping, just verify delivery works
    python main.py --check-config       # show which providers are configured
    python main.py --alert "text"       # send a one-off alert (used on CI failure)

Exit codes: 0 ok · 1 failed · 3 delivered only via the fallback channel.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from pathlib import Path

from src import config, db, investorgain_scraper, ipowatch_scraper, notifier, timing
from src.formatter import build_message
from src.utils import now_ist, today_ist

log = logging.getLogger("main")

EXIT_OK, EXIT_FAIL, EXIT_FALLBACK = 0, 1, 3
STATE_FILE = Path(os.getenv("STATE_FILE", ".state/sent"))


def configure_logging(verbose: bool = False) -> None:
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)-26s | %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fmt.converter = lambda ts: dt.datetime.fromtimestamp(ts, config.IST).timetuple()  # IST
    root = logging.getLogger()
    for handler in list(root.handlers):  # idempotent when called more than once
        if getattr(handler, "_ipo_tracker", False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console._ipo_tracker = True  # type: ignore[attr-defined]
    root.addHandler(console)
    try:  # a log file is uploaded as a CI artifact for post-mortems
        config.DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(config.DEBUG_DIR / "run.log", encoding="utf-8")
        file_handler.setFormatter(fmt)
        file_handler._ipo_tracker = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)
    except OSError:
        pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="IPO Tracker & WhatsApp Notifier")
    p.add_argument("--dry-run", action="store_true", help="print the message, don't send")
    p.add_argument("--date", help="override today's date (YYYY-MM-DD), for testing")
    p.add_argument("--test-message", action="store_true", help="send a test message only")
    p.add_argument("--check-config", action="store_true", help="show provider status and exit")
    p.add_argument("--alert", metavar="TEXT", help="send a one-off alert and exit")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p.parse_args(argv)


# --------------------------------------------------------------------------- #
# GitHub Actions helpers (no-ops locally)
# --------------------------------------------------------------------------- #


def gh_output(key: str, value: str) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


def gh_annotate(level: str, title: str, text: str) -> None:
    """Show a one-line notice/warning on the GitHub Actions run page."""
    if os.getenv("GITHUB_ACTIONS") == "true":
        clean = text.replace("%", "%25").replace("\r", "").replace("\n", " ")
        print(f"::{level} title={title}::{clean}", flush=True)


def gh_summary(text: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def mark_sent(run_date: dt.date, provider: str) -> None:
    """Marker cached by the workflow so later runs today don't send twice."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(f"{run_date.isoformat()} {provider} {now_ist().isoformat()}\n")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def deliver(message: str, *, dry_run: bool) -> tuple[int, str]:
    """Send and translate the outcome to an exit code. Sets the `reported`
    step output once the user has been told *something*, so the workflow's
    failure-alert step doesn't send a duplicate."""
    try:
        provider = notifier.send_whatsapp(message, dry_run=dry_run)
        gh_output("reported", "true")
        return EXIT_OK, provider
    except notifier.DeliveredViaFallback as exc:
        log.error("%s", exc)
        gh_output("reported", "true")
        return EXIT_FALLBACK, exc.provider
    except notifier.NotifierError as exc:
        log.error("DELIVERY FAILED: %s", exc)
        return EXIT_FAIL, "none"


def run_pipeline(args: argparse.Namespace) -> int:
    run_date = dt.date.fromisoformat(args.date) if args.date else today_ist()
    started = now_ist()
    log.info("Run started at %s IST (target date: %s)", started.strftime("%Y-%m-%d %H:%M:%S"),
             run_date)
    for line in notifier.describe_config():
        log.info("provider: %s", line)

    notes: list[str] = []

    # ------------------------------------------------------------ Step 1
    db.init_db(reset=True)
    try:
        candidates = investorgain_scraper.run(target_date=run_date)
    except Exception as exc:  # noqa: BLE001
        log.exception("STEP 1 failed")
        message = (
            f"⚠️ IPO alert for {run_date.strftime('%d-%b-%Y')} could not be built.\n"
            f"InvestorGain could not be read: {str(exc)[:200]}\n"
            "Please check investorgain.com manually today.\n"
            f"IPO Tracker v{config.APP_VERSION}"
        )
        deliver(message, dry_run=args.dry_run)
        gh_summary(f"### ❌ Step 1 failed\n```\n{exc}\n```")
        return EXIT_FAIL
    log.info("STEP 1 complete: %s candidate(s)", len(candidates))
    gh_annotate("notice", "InvestorGain", f"{len(candidates)} IPO(s) closing {run_date} above threshold")

    # ------------------------------------------------------------ Step 2
    if candidates and not config.IPOWATCH_ENABLED:
        log.info("STEP 2 skipped: IPOWATCH_ENABLED is false")
    elif candidates:
        try:
            results = ipowatch_scraper.run()
            matched = sum(1 for r in results if r["match"])
            log.info("STEP 2 complete: %s/%s matched on IPO Watch", matched, len(results))
            limit = config.IPOWATCH_DAILY_LIMIT or "∞"
            gh_annotate("notice", "IPO Watch",
                        f"matched {matched}/{len(results)} candidate(s) · request "
                        f"{ipowatch_scraper.quota_used()}/{limit} today")
        except ipowatch_scraper.QuotaExceeded as exc:
            log.warning("STEP 2 skipped: %s", exc)
            gh_annotate("warning", "IPO Watch limit", str(exc))
            notes.append(f"IPO Watch skipped: daily limit of {config.IPOWATCH_DAILY_LIMIT} "
                         "requests reached.")
        except ipowatch_scraper.SourceUnavailable as exc:
            log.warning("STEP 2 skipped: %s", exc)
            gh_annotate("warning", "IPO Watch unavailable", str(exc)[:300])
            notes.append("IPO Watch cross-check unavailable today.")

    rows = db.get_candidates()

    # ------------------------------------------------------------ Step 3
    send_time = now_ist()
    banner = None if args.date else timing.lateness_banner(
        send_time, config.SEND_DEADLINE, config.SUBSCRIPTION_CUTOFF)
    message = build_message(rows, run_date, run_time=send_time, banner=banner, notes=notes)
    log.info("Message built (%s chars, %s IPO(s))", len(message), len(rows))

    if not rows and not config.SEND_WHEN_EMPTY and not args.dry_run:
        log.info("No matches and SEND_WHEN_EMPTY is false — nothing sent.")
        return EXIT_OK

    code, provider = deliver(message, dry_run=args.dry_run)
    if code != EXIT_FAIL and not args.dry_run:
        mark_sent(run_date, provider)
    done = now_ist()
    log.info("STEP 3 %s via %s at %s IST", "complete" if code == EXIT_OK else "PROBLEM",
             provider, done.strftime("%H:%M:%S"))
    gh_summary(
        f"### {'✅' if code == EXIT_OK else '⚠️'} Alert {'sent' if code != EXIT_FAIL else 'NOT sent'}"
        f" via `{provider}` at {done.strftime('%H:%M:%S')} IST\n"
        f"Target: before {config.SEND_DEADLINE} IST · IPOs selected: {len(rows)}\n\n"
        f"```\n{message}\n```"
    )
    return code


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    if args.check_config:
        print("\n".join(notifier.describe_config()))
        return EXIT_OK

    if args.alert or args.test_message:
        text = args.alert or (
            f"✅ IPO Tracker test message — delivery works! "
            f"({now_ist().strftime('%d-%b %H:%M')} IST)"
        )
        code, provider = deliver(text, dry_run=args.dry_run)
        log.info("Sent via %s", provider)
        return code

    return run_pipeline(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - last-resort: never die silently
        logging.getLogger("main").exception("Pipeline crashed: %s", exc)
        raise SystemExit(EXIT_FAIL)
