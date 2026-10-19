"""Parsing, filtering, fuzzy matching and message layout — no network."""

from src import investorgain_scraper as ig
from src import ipowatch_scraper as iw
from src.formatter import build_message

from .conftest import IW_HTML, ig_html


def test_investorgain_parses_every_row(today):
    rows = ig.parse_rows(ig_html(today))
    assert [r["name"] for r in rows] == [
        "Alpha Cement Industries Ltd", "Beta Logistics Limited", "Gamma Foods & Beverages",
        "Delta Pharma Ltd", "Epsilon Agro",
    ]
    alpha = rows[0]
    assert alpha["issue_price"] == 114 and alpha["ig_gmp"] == 55
    assert alpha["ig_gmp_pct"] == 48.25 and alpha["exchange"] == "SME"
    assert alpha["close_date"] == today
    assert rows[3]["ig_gmp_pct"] == 23.15  # computed: 250 / 1080


def test_filter_uses_ist_date_and_per_type_thresholds(today):
    selected = ig.filter_rows(ig.parse_rows(ig_html(today)), target_date=today)
    # Alpha SME 48% > 40 ✓, Delta mainboard 23% > 15 ✓, Beta 5% ✗,
    # Epsilon SME 20% (<40) ✗, Gamma closes another day ✗
    assert [r["name"] for r in selected] == ["Alpha Cement Industries Ltd", "Delta Pharma Ltd"]


def test_ipowatch_fuzzy_match():
    entries = iw.parse_entries(IW_HTML)
    match, score = iw.best_match("alpha cement industries", entries)
    assert match["name"] == "Alpha Cement" and score >= 80
    match, _ = iw.best_match("delta pharma", entries)
    assert match["gmp"] == 240
    assert iw.best_match("omega steel", entries)[0] is None


def test_message_layout_with_banner_and_notes(today):
    rows = [{"name": "Alpha", "exchange": "SME", "ig_gmp": 55, "ig_gmp_pct": 48.25,
             "iw_gmp": None, "iw_gmp_pct": None}]
    msg = build_message(rows, today, banner="⚠️ Delayed", notes=["IPO Watch down."])
    assert msg.startswith("⚠️ Delayed")
    assert "Company Name: Alpha [SME]" in msg
    assert "InvestorGain GMP: ₹55 (48.25%)" in msg
    assert "IPO Watch GMP: Not listed" in msg
    assert "ℹ️ IPO Watch down." in msg
    assert "19-Oct-2026" in msg


def test_empty_day_message(today):
    msg = build_message([], today)
    assert "Nothing to apply for" in msg
