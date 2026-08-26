#!/usr/bin/env python3
"""Data-accuracy audit tool. Sends nothing, writes nothing to the real DB.

Shows every row both sites returned, the raw cell text next to the value the
parser extracted from it, and the reason each row passed or failed the filter —
so you can put this terminal window next to the website and compare.

    python verify.py                    # fetch both sites live and audit
    python verify.py --save             # also save raw HTML + CSV to debug/
    python verify.py --date 2026-08-20  # audit against a different "today"
    python verify.py --offline          # re-parse previously saved HTML (no network)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from src import investorgain_scraper as ig
from src import ipowatch_scraper as iw
from src.config import (
    DEBUG_DIR,
    FUZZY_THRESHOLD,
    INVESTORGAIN_URL,
    IPOWATCH_URL,
    IPOWATCH_USE_PLAYWRIGHT,
    MIN_GMP_PCT_MAINBOARD,
    MIN_GMP_PCT_SME,
)
from src.formatter import build_message
from src.utils import fetch_html, today_ist

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[1m", "\033[0m"
)


def hr(char: str = "─", width: int = 100) -> str:
    return char * width


def title(text: str) -> None:
    print(f"\n{BOLD}{text}{RESET}\n{hr()}")


def cut(text: Optional[str], width: int) -> str:
    text = (text or "").replace("\n", " ").strip() or "-"
    return text if len(text) <= width else text[: width - 1] + "…"


def num(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}"


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def load_html(name: str, url: str, *, offline: bool, save: bool,
              use_browser: Optional[bool] = None) -> str:
    path = DEBUG_DIR / f"{name}.html"
    if offline:
        if not path.exists():
            sys.exit(f"{RED}No saved HTML at {path}. Run once with --save first.{RESET}")
        print(f"{DIM}Reading saved HTML from {path}{RESET}")
        return path.read_text(encoding="utf-8")

    html = fetch_html(url, use_browser=use_browser)
    print(f"{DIM}Fetched {len(html):,} bytes from {url}{RESET}")
    if save:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        print(f"{DIM}Saved raw HTML to {path} — open it in a browser to inspect{RESET}")
    return html


def write_csv(name: str, rows: list[dict[str, Any]], fields: list[str]) -> None:
    if not rows:
        return
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / f"{name}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"{DIM}Wrote {path} — open in Excel/Numbers to compare against the site{RESET}")


# --------------------------------------------------------------------------- #
# Section 1: how the table was interpreted
# --------------------------------------------------------------------------- #


def audit_columns(html: str) -> bool:
    title("1. TABLE DETECTION — did we read the right columns?")
    info = ig.describe_columns(html)

    if not info["found"]:
        print(f"{RED}✗ No GMP table found in the HTML.{RESET}")
        print("  Either the layout changed or the table is rendered by JavaScript.")
        print("  Run with --save and open debug/investorgain.html: if the table is")
        print("  missing there too, you need USE_PLAYWRIGHT=true (see README).")
        return False

    print(f"Rows in table:      {info['row_count']}")
    print(f"Headers found:      {info['headers']}\n")
    print(f"{'Our field':<14}{'→ Column #':<12}{'Header text'}")
    print(hr("·"))
    for field in ("name", "price", "gmp", "est_listing", "close", "open"):
        idx = info["mapping"].get(field)
        if idx is None:
            flag = RED if field in ("name", "gmp", "close") else YELLOW
            print(f"{flag}{field:<14}{'NOT FOUND':<12}{RESET}")
        else:
            header = info["headers"][idx] if idx < len(info["headers"]) else "?"
            print(f"{GREEN}{field:<14}{RESET}{idx:<12}{header}")

    print(f"\n{BOLD}CHECK:{RESET} each 'Our field' must line up with the column of the")
    print("same meaning on the website. If 'gmp' points at the Est. Listing column,")
    print("every number below will be wrong.")
    return True


# --------------------------------------------------------------------------- #
# Section 2: row-by-row extraction
# --------------------------------------------------------------------------- #


def verdict(row: dict[str, Any], target: dt.date) -> tuple[str, str]:
    if row["close_date"] is None:
        return RED, "date unparsed"
    if row["close_date"] != target:
        return DIM, f"closes {row['close_date'].strftime('%d-%b')}"
    limit = ig.threshold_for(row.get("exchange"))
    pct = row.get("ig_gmp_pct")
    if pct is None:
        return RED, "no GMP%"
    if pct <= limit:
        return YELLOW, f"GMP {pct:.1f}% ≤ {limit:.0f}% ({row.get('exchange')})"
    return GREEN, f"✅ SELECTED (> {limit:.0f}%)"


def audit_investorgain(html: str, target: dt.date) -> list[dict[str, Any]]:
    rows = ig.parse_rows(html)

    title(f"2. INVESTORGAIN — all {len(rows)} rows (raw text → extracted value)")
    print(
        f"{'Company':<28}{'Type':<11}{'Price':<8}{'GMP':<8}{'GMP%':<8}"
        f"{'Open':<10}{'Close':<10}{'→Parsed':<12}Verdict"
    )
    print(hr())

    for row in rows:
        colour, note = verdict(row, target)
        parsed_close = row["close_date"].strftime("%d-%b-%y") if row["close_date"] else "FAIL"
        print(
            f"{colour}{cut(row['name'], 27):<28}"
            f"{cut(row.get('exchange'), 10):<11}"
            f"{num(row['issue_price']):<8}{num(row['ig_gmp']):<8}"
            f"{num(row['ig_gmp_pct']):<8}"
            f"{cut(row.get('raw_open'), 9):<10}{cut(row['raw_close'], 9):<10}"
            f"{parsed_close:<12}{note}{RESET}"
        )

    selected = ig.filter_rows(rows, target_date=target)
    print(hr())
    print(f"Today (IST target date): {BOLD}{target.strftime('%d-%b-%Y')}{RESET}")
    sme = sum(1 for r in selected if (r.get("exchange") or "") == "SME")
    print(f"Thresholds: {BOLD}SME > {MIN_GMP_PCT_SME:.0f}%{RESET}, "
          f"{BOLD}Mainboard > {MIN_GMP_PCT_MAINBOARD:.0f}%{RESET}")
    print(f"Rows parsed: {len(rows)}   Closing today: "
          f"{sum(1 for r in rows if r['close_date'] == target)}   "
          f"{GREEN}Selected: {len(selected)} ({sme} SME, {len(selected) - sme} Mainboard){RESET}")

    print(f"\n{BOLD}CHECK:{RESET}")
    print("  • The Type column says SME for SME/BSE-SME/NSE-SME rows only")
    print("  • Company names are clean, with no trailing 'IPO O' badge")
    print("  • The Open and Close cells are NOT swapped — compare to the website")
    print("  • GMP% ≈ the gain % the site itself shows in Est. Listing")
    print("  • No 'FAIL' in the →Parsed close column")
    return rows


# --------------------------------------------------------------------------- #
# Section 3: cross-validation
# --------------------------------------------------------------------------- #


def audit_ipowatch(html: str, selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = iw.parse_entries(html)

    title(f"3. IPO WATCH — all {len(entries)} rows parsed")
    print(f"{'Company':<38}{'GMP cell':<14}{'→Used':<9}{'Price':<10}{'Gain%'}")
    print(hr("·"))
    for e in entries:
        print(
            f"{cut(e['name'], 37):<38}{cut(e['raw_gmp'], 13):<14}"
            f"{num(e['gmp']):<9}{num(e['price']):<10}{num(e['gmp_pct'])}"
        )

    title(f"4. FUZZY NAME MATCHING (threshold {FUZZY_THRESHOLD}, engine: {iw._MATCHER})")
    if not selected:
        print(f"{DIM}No IPOs selected in step 2, so nothing to match today.{RESET}")
        return entries

    print(f"{'InvestorGain name':<34}{'→ IPO Watch match':<34}{'Score':<8}Result")
    print(hr("·"))
    for row in selected:
        match, score = iw.best_match(row["normalized_name"], entries)
        if match:
            colour, result = GREEN, "matched"
        elif score >= FUZZY_THRESHOLD - 15:
            colour, result = YELLOW, f"below threshold — try FUZZY_THRESHOLD={int(score) - 5}"
        else:
            colour, result = DIM, "not on IPO Watch"
        print(
            f"{colour}{cut(row['name'], 33):<34}"
            f"{cut(match['name'] if match else '—', 33):<34}"
            f"{score:<8.1f}{result}{RESET}"
        )

    print(f"\n{BOLD}CHECK:{RESET} every yellow row is a name that IS on ipowatch.in but")
    print("scored too low. Lower FUZZY_THRESHOLD if you see these repeatedly.")
    return entries


# --------------------------------------------------------------------------- #
# Section 4: final message
# --------------------------------------------------------------------------- #


def audit_message(selected: list[dict[str, Any]], entries: list[dict[str, Any]],
                  target: dt.date) -> None:
    enriched: list[dict[str, Any]] = []
    for row in selected:
        match, _ = iw.best_match(row["normalized_name"], entries)
        enriched.append(
            {
                **row,
                "iw_gmp": match["gmp"] if match else None,
                "iw_gmp_pct": match["gmp_pct"] if match else None,
            }
        )

    title("5. MESSAGE THAT WOULD BE SENT (nothing was actually sent)")
    print(build_message(enriched, target))


# --------------------------------------------------------------------------- #


def main() -> int:
    p = argparse.ArgumentParser(description="Audit scraped IPO data accuracy")
    p.add_argument("--save", action="store_true", help="save raw HTML and CSV to debug/")
    p.add_argument("--offline", action="store_true", help="re-parse saved HTML, no network")
    p.add_argument("--date", help="treat this YYYY-MM-DD as today")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    target = dt.date.fromisoformat(args.date) if args.date else today_ist()

    print(f"{BOLD}IPO DATA AUDIT{RESET}  ({'offline' if args.offline else 'live fetch'})")

    ig_html = load_html("investorgain", INVESTORGAIN_URL, offline=args.offline, save=args.save)
    if not audit_columns(ig_html):
        return 1

    rows = audit_investorgain(ig_html, target)
    selected = ig.filter_rows(rows, target_date=target)
    if args.save:
        write_csv("investorgain", rows,
                  ["name", "raw_price", "issue_price", "raw_gmp", "ig_gmp",
                   "ig_gmp_pct", "raw_close", "close_date"])

    iw_html = load_html("ipowatch", IPOWATCH_URL, offline=args.offline, save=args.save,
                        use_browser=IPOWATCH_USE_PLAYWRIGHT)
    entries = audit_ipowatch(iw_html, selected)
    if args.save:
        write_csv("ipowatch", entries,
                  ["name", "raw_gmp", "gmp", "raw_price", "price", "gmp_pct"])

    audit_message(selected, entries, target)
    print(f"\n{DIM}No WhatsApp message was sent and the production DB was untouched.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
