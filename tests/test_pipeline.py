"""End-to-end pipeline runs (scrapers fed fixture HTML, delivery captured),
plus the timing rules that decide *when* the alert goes out."""

import datetime as dt
import re
from pathlib import Path

import pytest

import main
from src import config, investorgain_scraper, ipowatch_scraper, notifier, timing

from .conftest import IST, IW_HTML, ig_html

WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/daily_ipo.yml"


@pytest.fixture
def sent(monkeypatch):
    """Capture outgoing messages instead of calling a provider."""
    box: list[str] = []

    def fake_send(message, *, dry_run=False, **_):
        box.append(message)
        return "callmebot"

    monkeypatch.setattr(notifier, "send_whatsapp", fake_send)
    return box


def freeze(monkeypatch, hhmm, day=dt.date(2026, 10, 19)):
    moment = dt.datetime.combine(day, timing.parse_hhmm(hhmm), tzinfo=IST)
    monkeypatch.setattr(main, "now_ist", lambda: moment)
    monkeypatch.setattr(main, "today_ist", lambda: day)
    monkeypatch.setattr(investorgain_scraper, "today_ist", lambda: day)
    return moment


def feed(monkeypatch, today, *, ig=None, iw=None):
    def ig_fetch(url, **_):
        if isinstance(ig, Exception):
            raise ig
        return ig if ig is not None else ig_html(today)

    def iw_fetch(url, **_):
        if isinstance(iw, Exception):
            raise iw
        return iw if iw is not None else IW_HTML

    monkeypatch.setattr(investorgain_scraper, "fetch_html", ig_fetch)
    monkeypatch.setattr(ipowatch_scraper, "fetch_html", iw_fetch)


# --------------------------------------------------------------- happy path


def test_full_run_on_time_sends_once_and_marks_day(monkeypatch, sent, today):
    freeze(monkeypatch, "13:31")
    feed(monkeypatch, today)
    assert main.main([]) == main.EXIT_OK
    assert len(sent) == 1
    msg = sent[0]
    assert "Alpha Cement Industries Ltd [SME]" in msg
    assert "IPO Watch GMP: ₹54 (47.37%)" in msg
    assert "Delta Pharma Ltd [Mainboard]" in msg
    assert "sent 13:31 IST" in msg
    assert "Delayed" not in msg and "LATE" not in msg
    assert Path(".state/sent").read_text().startswith("2026-10-19 callmebot")
    assert Path("debug/run.log").exists()


def test_late_run_carries_a_banner(monkeypatch, sent, today):
    freeze(monkeypatch, "18:40")  # what the old GitHub cron actually did
    feed(monkeypatch, today)
    main.main([])
    assert sent[0].startswith("⚠️ LATE ALERT (18:40 IST)")


# --------------------------------------------------------------- failures are loud


def test_investorgain_down_still_messages_user_and_fails_run(monkeypatch, sent, today):
    freeze(monkeypatch, "13:31")
    feed(monkeypatch, today, ig=RuntimeError("Could not fetch after 3 attempts"))
    assert main.main([]) == main.EXIT_FAIL
    assert len(sent) == 1 and "InvestorGain could not be read" in sent[0]
    assert not Path(".state/sent").exists()  # a later backup run may retry


def test_ipowatch_down_degrades_gracefully(monkeypatch, sent, today):
    freeze(monkeypatch, "13:31")
    feed(monkeypatch, today, iw=RuntimeError("timeout"))
    assert main.main([]) == main.EXIT_OK
    assert "IPO Watch cross-check unavailable today" in sent[0]
    assert "Alpha Cement Industries Ltd" in sent[0]


def test_delivery_failure_exits_nonzero(monkeypatch, today):
    freeze(monkeypatch, "13:31")
    feed(monkeypatch, today)
    # no provider secrets configured at all (conftest clears them)
    assert main.main([]) == main.EXIT_FAIL
    assert not Path(".state/sent").exists()


def test_github_outputs_and_summary(monkeypatch, sent, today, tmp_path):
    out, summary = tmp_path / "out", tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    freeze(monkeypatch, "13:31")
    feed(monkeypatch, today)
    main.main([])
    assert "reported=true" in out.read_text()
    assert "Alert sent via `callmebot` at 13:31:00 IST" in summary.read_text()


# --------------------------------------------------------------- timing rules


@pytest.mark.parametrize("arrives, expected", [
    ("09:00", "skip"),   # too early: a later backup cron will cover the day
    ("11:00", "wait"),   # sleeps until 13:40, then sends
    ("13:39", "wait"),
    ("13:40", "go"),
    ("19:30", "go"),     # very late: still sends (with a banner) rather than stay silent
])
def test_backup_gate(arrives, expected):
    now = dt.datetime.combine(dt.date(2026, 10, 19), timing.parse_hhmm(arrives), tzinfo=IST)
    assert timing.wait_plan(now, "13:40", 210)[0] == expected


def test_gate_works_from_utc_clock():
    """Runners use UTC; 08:10 UTC is 13:40 IST."""
    now_utc = dt.datetime(2026, 10, 19, 8, 10, tzinfo=dt.timezone.utc)
    assert timing.wait_plan(now_utc, "13:40", 210) == ("go", 0.0)


def test_lateness_banner_thresholds():
    day = dt.date(2026, 10, 19)
    at = lambda t: dt.datetime.combine(day, timing.parse_hhmm(t), tzinfo=IST)  # noqa: E731
    assert timing.lateness_banner(at("13:59"), "14:00", "17:00") is None
    assert "Delayed" in timing.lateness_banner(at("14:20"), "14:00", "17:00")
    assert "LATE" in timing.lateness_banner(at("17:05"), "14:00", "17:00")


def test_workflow_crons_are_weekday_mornings_ist():
    """Every backup cron must fire on an IST weekday before the 14:00 deadline."""
    crons = re.findall(r'cron:\s*"([^"]+)"', WORKFLOW.read_text())
    assert crons, "no schedule found"
    for expr in crons:
        minute, hours, _, _, dow = expr.split()
        assert dow == "1-5"
        for hour in hours.split(","):
            utc = dt.datetime(2026, 10, 19, int(hour), int(minute), tzinfo=dt.timezone.utc)
            ist = utc.astimezone(IST)
            assert ist.date() == utc.date(), f"{expr} lands on a different IST day"
            assert ist.time() < dt.time(14, 0), f"{expr} = {ist:%H:%M} IST is too late"


def test_config_reads_send_deadline_default():
    assert config.SEND_DEADLINE == "14:00"
