"""STEP 2 — Cross-validation.

Reads the company names Step 1 stored in SQLite, scrapes every GMP row from
IPO Watch, then fuzzy-matches the two name sets (the sites format names
differently: 'Acme Industries Ltd IPO (SME)' vs 'Acme Industries IPO').

rapidfuzz is used when available (fast, wheel-only, no compiler); difflib from
the standard library is the automatic fallback so the pipeline never hard-fails
on a dependency."""

from __future__ import annotations

import logging
from typing import Any, Optional

from bs4 import BeautifulSoup

from . import db
from .config import FUZZY_THRESHOLD, IPOWATCH_URL
from .utils import (
    clean_display_name,
    compute_gmp_pct,
    dump_debug_html,
    fetch_html,
    normalize_name,
    parse_amount,
    parse_percent,
    parse_upper_price,
)

log = logging.getLogger(__name__)

try:
    from rapidfuzz import fuzz as _fuzz

    def _raw_similarity(a: str, b: str) -> float:
        # token_set_ratio ignores word order and extra tokens like "Ltd"/"SME".
        return float(_fuzz.token_set_ratio(a, b))

    _MATCHER = "rapidfuzz"
except ImportError:  # pragma: no cover - exercised only when the wheel is absent
    from difflib import SequenceMatcher

    def _raw_similarity(a: str, b: str) -> float:
        """Token-set-aware stand-in for rapidfuzz.token_set_ratio.

        Plain SequenceMatcher scores 'alpha cement industries' vs 'alpha cement'
        at only ~69, which would wrongly drop a valid match. Comparing token
        *sets* fixes that while staying in the standard library."""
        ta, tb = set(a.split()), set(b.split())
        if not ta or not tb:
            return 0.0
        smaller = min(len(ta), len(tb))
        if smaller >= 2 and (ta <= tb or tb <= ta):
            return 100.0
        sequence = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio() * 100
        overlap = len(ta & tb) / smaller * 100 * 0.95
        return max(sequence, overlap)

    _MATCHER = "difflib"


def _similarity(a: str, b: str) -> float:
    """Similarity with a guard against over-eager one-word matches
    (e.g. 'beta' matching both 'Beta Foods' and 'Beta Logistics')."""
    if a == b:
        return 100.0
    score = _raw_similarity(a, b)
    if min(len(a.split()), len(b.split())) < 2:
        score *= 0.85
    return score


def _cell_text(cell) -> str:
    return " ".join(cell.get_text(" ", strip=True).split())


def _map_columns(headers: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, header in enumerate(headers):
        h = header.lower()
        if "name" not in mapping and ("ipo" in h or "company" in h or "name" in h):
            mapping["name"] = idx
        elif "gmp" not in mapping and ("gmp" in h or "premium" in h):
            mapping["gmp"] = idx
        elif "price" not in mapping and ("price" in h or "band" in h):
            mapping["price"] = idx
        elif "gain" not in mapping and ("gain" in h or "%" in h or "listing" in h):
            mapping["gain"] = idx
    return mapping


def parse_entries(html: str) -> list[dict[str, Any]]:
    """Extract every (name, gmp, price) triple from all GMP tables on the page."""
    soup = BeautifulSoup(html, "lxml")
    entries: list[dict[str, Any]] = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        header_cells = rows[0].find_all(["th", "td"])
        headers = [_cell_text(c) for c in header_cells]
        cols = _map_columns(headers)
        if "name" not in cols or "gmp" not in cols:
            continue

        for tr in rows[1:]:
            cells = [_cell_text(c) for c in tr.find_all(["td", "th"])]
            if len(cells) < 2:
                continue

            def col(field: str) -> str:
                idx = cols.get(field)
                return cells[idx] if idx is not None and idx < len(cells) else ""

            raw_name = col("name")
            if not raw_name or len(raw_name) < 3:
                continue

            gmp = parse_amount(col("gmp"))
            price = parse_upper_price(col("price"))
            gain_pct = parse_percent(col("gain")) or parse_percent(col("gmp"))
            if gain_pct is None:
                gain_pct = compute_gmp_pct(gmp, price)

            entries.append(
                {
                    "name": clean_display_name(raw_name),
                    "raw_name": raw_name,
                    "normalized_name": normalize_name(raw_name),
                    "gmp": gmp,
                    "price": price,
                    "gmp_pct": gain_pct,
                    "raw_gmp": col("gmp"),
                    "raw_price": col("price"),
                }
            )

    # De-duplicate by normalised name, keeping the first (most current) occurrence.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for e in entries:
        key = e["normalized_name"]
        if key and key not in seen:
            seen.add(key)
            unique.append(e)

    log.info("Parsed %s unique IPO row(s) from IPO Watch", len(unique))
    return unique


def best_match(
    normalized_name: str,
    entries: list[dict[str, Any]],
    *,
    threshold: int = FUZZY_THRESHOLD,
) -> tuple[Optional[dict[str, Any]], float]:
    """Highest-scoring IPO Watch entry for a given normalised name."""
    if not normalized_name or not entries:
        return None, 0.0

    best, best_score = None, 0.0
    for entry in entries:
        score = _similarity(normalized_name, entry["normalized_name"])
        if score > best_score:
            best, best_score = entry, score

    if best_score < threshold:
        return None, best_score
    return best, best_score


def run(*, persist: bool = True) -> list[dict[str, Any]]:
    """Full Step 2: read candidates -> scrape -> fuzzy match -> enrich SQLite."""
    candidates = db.get_candidate_names()
    if not candidates:
        log.info("No candidates from Step 1 — skipping IPO Watch entirely")
        return []

    log.info("Cross-validating %s candidate(s) using %s", len(candidates), _MATCHER)
    html = fetch_html(IPOWATCH_URL)
    entries = parse_entries(html)
    if not entries:
        dump_debug_html("ipowatch_empty", html)
        log.warning("IPO Watch returned no parsable rows; continuing without it")

    results: list[dict[str, Any]] = []
    for ipo_id, name, norm in candidates:
        match, score = best_match(norm, entries)
        if match:
            log.info("Matched '%s' -> '%s' (score %.1f)", name, match["name"], score)
        else:
            log.warning("No IPO Watch match for '%s' (best score %.1f)", name, score)

        if persist:
            db.update_ipowatch(
                ipo_id,
                iw_name=match["name"] if match else None,
                iw_gmp=match["gmp"] if match else None,
                iw_gmp_pct=match["gmp_pct"] if match else None,
                score=round(score, 1),
            )
        results.append({"id": ipo_id, "name": name, "match": match, "score": score})

    return results


if __name__ == "__main__":  # python -m src.ipowatch_scraper (run after step 1)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for r in run():
        print(r["name"], "->", (r["match"] or {}).get("name"), r["score"])
