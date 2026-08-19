"""STEP 1 — Primary source.

Scrapes the InvestorGain live IPO GMP report, keeps only rows where
    * Close Date == today (IST), and
    * (GMP / Issue Price) * 100 > MIN_GMP_PCT
and writes the survivors into SQLite.

Column positions on this page move around, so we map columns by *header text*
rather than by index. If the headers ever change wording, only `_COLUMN_HINTS`
below needs editing."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Optional

from bs4 import BeautifulSoup

from . import db
from .config import INVESTORGAIN_URL, MIN_GMP_PCT
from .utils import (
    clean_display_name,
    compute_gmp_pct,
    dump_debug_html,
    fetch_html,
    normalize_name,
    parse_amount,
    parse_close_date,
    parse_percent,
    parse_upper_price,
    today_ist,
)

log = logging.getLogger(__name__)

# Header keyword -> logical field, matched as a substring of the lower-cased header.
# The second tuple is anti-hints: a header containing any of them is rejected for
# that field. This exists because "IPO Size" would otherwise claim the name column.
_COLUMN_HINTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "name": (("name", "company", "ipo"), ("size", "lot", "price", "gmp", "date")),
    "price": (("price", "band"), ("size",)),
    "gmp": (("gmp", "premium"), ("updated", "date")),
    "est_listing": (("est listing", "estimated", "listing gain"), ()),
    "close": (("close", "closing"), ()),
    "open": (("open", "opening"), ()),
}


def _cell_text(cell) -> str:
    return " ".join(cell.get_text(" ", strip=True).split())


def _pick_table(soup: BeautifulSoup):
    """Return the table that actually contains IPO rows (the page has several)."""
    best, best_score = None, -1
    for table in soup.find_all("table"):
        headers = " ".join(_cell_text(th).lower() for th in table.find_all("th"))
        score = sum(kw in headers for kw in ("ipo", "gmp", "close", "price"))
        rows = len(table.find_all("tr"))
        score = score * 10 + min(rows, 50)
        if "gmp" in headers and score > best_score:
            best, best_score = table, score
    return best


def _map_columns(table) -> dict[str, int]:
    header_row = table.find("thead")
    header_cells = (header_row or table).find_all("th")
    if not header_cells:
        first_row = table.find("tr")
        header_cells = first_row.find_all(["th", "td"]) if first_row else []

    headers = [_cell_text(c).lower() for c in header_cells]
    mapping: dict[str, int] = {}
    for field, (hints, anti) in _COLUMN_HINTS.items():
        for idx, header in enumerate(headers):
            if idx in mapping.values():
                continue
            if any(bad in header for bad in anti):
                continue
            if any(hint in header for hint in hints):
                mapping[field] = idx
                break
    log.debug("InvestorGain headers=%s mapping=%s", headers, mapping)
    return mapping


def _parse_row(cells: list[str], cols: dict[str, int]) -> Optional[dict[str, Any]]:
    def col(field: str) -> str:
        idx = cols.get(field)
        return cells[idx] if idx is not None and idx < len(cells) else ""

    raw_name = col("name")
    if not raw_name:
        return None

    price = parse_upper_price(col("price"))
    gmp = parse_amount(col("gmp"))
    # InvestorGain sometimes prints the gain % in the GMP or Est. Listing cell.
    gmp_pct = parse_percent(col("gmp")) or parse_percent(col("est_listing"))
    if gmp_pct is None:
        gmp_pct = compute_gmp_pct(gmp, price)

    close_date = parse_close_date(col("close"))
    exchange = "SME" if "sme" in raw_name.lower() else "Mainboard"

    display = clean_display_name(raw_name)
    return {
        "name": display,
        "raw_name": raw_name,
        "normalized_name": normalize_name(raw_name),
        "issue_price": price,
        "ig_gmp": gmp,
        "ig_gmp_pct": gmp_pct,
        "close_date": close_date,
        "exchange": exchange,
        # Raw cell text is kept so verify.py can show extraction next to source.
        "raw_price": col("price"),
        "raw_gmp": col("gmp"),
        "raw_close": col("close"),
        "raw_open": col("open"),
        "raw_est_listing": col("est_listing"),
    }


def describe_columns(html: str) -> dict[str, Any]:
    """Report which table was chosen and how its headers mapped to our fields.
    Used by verify.py — this is the first thing to check when output looks wrong."""
    soup = BeautifulSoup(html, "lxml")
    table = _pick_table(soup)
    if table is None:
        return {"found": False, "headers": [], "mapping": {}, "row_count": 0}

    header_row = table.find("thead") or table
    header_cells = header_row.find_all("th") or (table.find("tr").find_all(["th", "td"]))
    body = table.find("tbody") or table
    return {
        "found": True,
        "headers": [_cell_text(c) for c in header_cells],
        "mapping": _map_columns(table),
        "row_count": len(body.find_all("tr")),
    }


def parse_rows(html: str) -> list[dict[str, Any]]:
    """Parse every IPO row on the page (no filtering yet)."""
    soup = BeautifulSoup(html, "lxml")
    table = _pick_table(soup)
    if table is None:
        dump_debug_html("investorgain_no_table", html)
        raise RuntimeError(
            "No GMP table found on InvestorGain. The page layout may have changed, "
            "or it now renders via JavaScript — try USE_PLAYWRIGHT=true."
        )

    cols = _map_columns(table)
    if "name" not in cols or "gmp" not in cols:
        dump_debug_html("investorgain_bad_headers", html)
        raise RuntimeError(f"Could not locate required columns. Found: {cols}")

    body = table.find("tbody") or table
    rows: list[dict[str, Any]] = []
    for tr in body.find_all("tr"):
        cells = [_cell_text(td) for td in tr.find_all(["td", "th"])]
        if len(cells) < 3:
            continue
        parsed = _parse_row(cells, cols)
        if parsed and parsed["name"]:
            rows.append(parsed)

    log.info("Parsed %s row(s) from InvestorGain", len(rows))
    return rows


def filter_rows(
    rows: list[dict[str, Any]],
    *,
    target_date: Optional[dt.date] = None,
    min_gmp_pct: float = MIN_GMP_PCT,
) -> list[dict[str, Any]]:
    """Apply the two business rules: closing today AND GMP% above threshold."""
    target = target_date or today_ist()
    kept: list[dict[str, Any]] = []

    for row in rows:
        if row["close_date"] != target:
            continue
        pct = row.get("ig_gmp_pct")
        if pct is None or pct <= min_gmp_pct:
            log.info(
                "Skipping %s — closes today but GMP%% is %s (need > %s)",
                row["name"], pct, min_gmp_pct,
            )
            continue
        kept.append(row)

    log.info(
        "%s IPO(s) close on %s with GMP%% > %s", len(kept), target.isoformat(), min_gmp_pct
    )
    return kept


def run(*, target_date: Optional[dt.date] = None, persist: bool = True) -> list[dict[str, Any]]:
    """Full Step 1: fetch -> parse -> filter -> store in SQLite."""
    html = fetch_html(INVESTORGAIN_URL)
    all_rows = parse_rows(html)
    if not all_rows:
        dump_debug_html("investorgain_empty", html)
    matches = filter_rows(all_rows, target_date=target_date)
    if persist:
        db.save_candidates(matches)
    return matches


if __name__ == "__main__":  # manual debugging: python -m src.investorgain_scraper
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    db.init_db()
    for r in run():
        print(f"{r['name']:<45} ₹{r['ig_gmp']} ({r['ig_gmp_pct']}%)")
