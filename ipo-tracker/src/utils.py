"""Shared low-level helpers: HTTP fetching, number parsing, date parsing,
company-name normalisation. Deliberately dependency-light and defensive —
scraped HTML is messy and changes without warning."""

from __future__ import annotations

import datetime as dt
import logging
import re
import time
from typing import Iterable, Optional

import requests

from .config import (
    DEBUG_DIR,
    HTTP_RETRIES,
    HTTP_TIMEOUT,
    IST,
    USER_AGENT,
    USE_PLAYWRIGHT,
)

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #


def now_ist() -> dt.datetime:
    return dt.datetime.now(IST)


def today_ist() -> dt.date:
    return now_ist().date()


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def http_get(url: str, *, headers: Optional[dict] = None, retries: int = HTTP_RETRIES) -> str:
    """GET a URL with retries + exponential backoff. Returns decoded HTML."""
    merged = {**DEFAULT_HEADERS, **(headers or {})}
    last_error: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            log.info("GET %s (attempt %s/%s)", url, attempt, retries)
            resp = requests.get(url, headers=merged, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            resp.encoding = resp.encoding or "utf-8"
            return resp.text
        except Exception as exc:  # noqa: BLE001 - we genuinely want to retry anything
            last_error = exc
            log.warning("Fetch failed: %s", exc)
            if attempt < retries:
                time.sleep(2 ** attempt)

    raise RuntimeError(f"Could not fetch {url} after {retries} attempts") from last_error


def fetch_html(url: str, *, use_browser: Optional[bool] = None) -> str:
    """Fetch a page. `use_browser` overrides the global USE_PLAYWRIGHT flag, so a
    site that renders fine with plain HTTP is never routed through Chromium."""
    if use_browser is None:
        use_browser = USE_PLAYWRIGHT
    if not use_browser:
        return http_get(url)

    from playwright.sync_api import sync_playwright  # imported lazily

    log.info("Fetching %s via Playwright (headless Chromium)", url)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        # "networkidle" never fires on pages with ads/analytics that poll forever,
        # so wait for the DOM and then for an actual table to appear instead.
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_selector("table tbody tr", timeout=20_000)
        except Exception:  # noqa: BLE001 - fall through and parse whatever loaded
            log.warning("No table row appeared within 20s; parsing current DOM")
        page.wait_for_timeout(1_500)  # let any final rows render
        html = page.content()
        browser.close()
    return html


def dump_debug_html(name: str, html: str) -> None:
    """Persist raw HTML so a failed run can be diagnosed from the CI artifact."""
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        path = DEBUG_DIR / f"{name}.html"
        path.write_text(html, encoding="utf-8")
        log.info("Wrote debug HTML to %s", path)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not write debug HTML: %s", exc)


# --------------------------------------------------------------------------- #
# Number parsing
# --------------------------------------------------------------------------- #

_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_PCT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")


def _to_float(raw: str) -> Optional[float]:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def parse_numbers(text: str) -> list[float]:
    """Every number in a string, commas stripped. '₹108 to ₹114' -> [108.0, 114.0]"""
    if not text:
        return []
    values = [_to_float(m.group()) for m in _NUM_RE.finditer(text)]
    return [v for v in values if v is not None]


def parse_amount(text: str) -> Optional[float]:
    """First number in a string. Used for GMP cells like '₹45' or '45 (39%)'."""
    nums = parse_numbers(text)
    return nums[0] if nums else None


def parse_upper_price(text: str) -> Optional[float]:
    """Upper band of a price range — the cap price is what applicants pay."""
    nums = [n for n in parse_numbers(text) if n > 0]
    return max(nums) if nums else None


def parse_percent(text: str) -> Optional[float]:
    """Explicit percentage inside a string, e.g. 'Est ₹159 (39.47%)' -> 39.47"""
    if not text:
        return None
    m = _PCT_RE.search(text)
    return float(m.group(1)) if m else None


def compute_gmp_pct(gmp: Optional[float], price: Optional[float]) -> Optional[float]:
    """(GMP / Issue Price) * 100 — the rule that drives the whole filter."""
    if gmp is None or not price:
        return None
    return round((gmp / price) * 100, 2)


# --------------------------------------------------------------------------- #
# Date parsing
# --------------------------------------------------------------------------- #

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_DAY_MON_RE = re.compile(
    r"(\d{1,2})\s*[-/ ]\s*([A-Za-z]{3,9})\s*(?:[-/ ]\s*(\d{2,4}))?"
)
_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DMY_RE = re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})")


def parse_close_date(text: str, reference: Optional[dt.date] = None) -> Optional[dt.date]:
    """Parse the many shapes a close date takes: '22-Aug', '22 Aug 2026',
    '2026-08-22', '22/08/2026'. Year is inferred (with wrap-around) when absent."""
    if not text:
        return None
    text = text.strip()
    ref = reference or today_ist()

    if (m := _ISO_RE.search(text)):
        y, mo, d = (int(g) for g in m.groups())
        return _safe_date(y, mo, d)

    if (m := _DAY_MON_RE.search(text)):
        day = int(m.group(1))
        month = _MONTHS.get(m.group(2)[:4].lower()) or _MONTHS.get(m.group(2)[:3].lower())
        if month:
            year_raw = m.group(3)
            if year_raw:
                year = int(year_raw)
                year += 2000 if year < 100 else 0
            else:
                year = ref.year
                # Handle a Dec/Jan boundary: '30-Dec' seen in early January.
                if month == 12 and ref.month == 1:
                    year -= 1
                elif month == 1 and ref.month == 12:
                    year += 1
            return _safe_date(year, month, day)

    if (m := _DMY_RE.search(text)):
        d, mo, y = (int(g) for g in m.groups())
        y += 2000 if y < 100 else 0
        return _safe_date(y, mo, d)

    return None


def _safe_date(year: int, month: int, day: int) -> Optional[dt.date]:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Company-name normalisation
# --------------------------------------------------------------------------- #

_NOISE_WORDS = {
    "ipo", "limited", "ltd", "pvt", "private", "india", "indian",
    "sme", "nse", "bse", "mainboard", "main", "board", "company", "co",
    "and", "the", "of", "&",
}

_STATUS_TOKENS = re.compile(
    r"\b(upcoming|open|close[ds]?|closing|listed|listing|allotment|new|live|today)\b",
    re.IGNORECASE,
)


def normalize_name(name: str) -> str:
    """Aggressively normalise a company name so InvestorGain's
    'Acme Industries Ltd IPO (SME)' matches IPO Watch's 'Acme Industries IPO'."""
    if not name:
        return ""
    text = name.lower()
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", text)      # drop bracketed annotations
    text = _STATUS_TOKENS.sub(" ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [t for t in text.split() if t and t not in _NOISE_WORDS]
    return " ".join(tokens)


def clean_display_name(name: str) -> str:
    """Tidy a name for the WhatsApp message without destroying it.
    'Alpha Cement Industries Ltd IPO (SME)' -> 'Alpha Cement Industries Ltd'"""
    text = re.sub(r"\s+", " ", (name or "").strip())
    # Drop trailing annotations such as "(SME)", "(NSE SME)", "(Mainboard)".
    text = re.sub(r"\s*\((?:[^()]*)\)\s*$", "", text).strip()
    text = _STATUS_TOKENS.sub(" ", text)
    # Drop a trailing "IPO" (possibly repeated after the bracket removal).
    for _ in range(2):
        text = re.sub(r"\bIPO\b\s*$", "", text, flags=re.IGNORECASE).strip(" -–—,")
    text = re.sub(r"\s+", " ", text).strip()
    return text or re.sub(r"\s+", " ", (name or "").strip())


def first_non_empty(values: Iterable[Optional[str]]) -> Optional[str]:
    for v in values:
        if v and v.strip():
            return v.strip()
    return None
