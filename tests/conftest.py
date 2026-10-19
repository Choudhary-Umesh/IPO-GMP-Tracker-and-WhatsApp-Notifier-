import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import config  # noqa: E402

IST = config.IST


def d(day: dt.date) -> str:
    return day.strftime("%d-%b")


def ig_html(today: dt.date) -> str:
    """InvestorGain-shaped fixture: one SME and one mainboard IPO qualify today."""
    y, t2 = today - dt.timedelta(days=2), today + dt.timedelta(days=2)
    return f"""
<html><body>
<table id="nav"><tr><th>Menu</th></tr><tr><td>Home</td></tr></table>
<table id="mainTable">
<thead><tr>
 <th>IPO</th><th>Price</th><th>GMP</th><th>Est Listing</th>
 <th>IPO Size</th><th>Lot</th><th>Open</th><th>Close</th><th>Listing</th>
</tr></thead>
<tbody>
 <tr><td>Alpha Cement Industries Ltd BSE SME O</td><td>₹100 to ₹114</td><td>₹55</td>
     <td>₹169 (48.25%)</td><td>₹50 Cr</td><td>1200</td><td>{d(y)}</td><td>{d(today)}</td><td>-</td></tr>
 <tr><td>Beta Logistics Limited IPO</td><td>₹200</td><td>₹10</td>
     <td>₹210 (5.00%)</td><td>₹120 Cr</td><td>75</td><td>{d(y)}</td><td>{d(today)}</td><td>-</td></tr>
 <tr><td>Gamma Foods &amp; Beverages IPO</td><td>₹90 to ₹95</td><td>₹30</td>
     <td>₹125</td><td>₹30 Cr</td><td>1600</td><td>{d(today)}</td><td>{d(t2)}</td><td>-</td></tr>
 <tr><td>Delta Pharma Ltd IPO O</td><td>₹1,080</td><td>₹250</td>
     <td>₹1,330</td><td>₹75 Cr</td><td>100</td><td>{d(y)}</td><td>{d(today)}</td><td>-</td></tr>
 <tr><td>Epsilon Agro NSE SME</td><td>₹50</td><td>₹10</td>
     <td>₹60 (20.00%)</td><td>₹10 Cr</td><td>2000</td><td>{d(y)}</td><td>{d(today)}</td><td>-</td></tr>
</tbody></table></body></html>
"""


IW_HTML = """
<html><body>
<table><tr><th>Current IPO</th><th>IPO GMP</th><th>Price</th><th>Listing Gain</th></tr>
 <tr><td>Alpha Cement IPO</td><td>₹54</td><td>₹114</td><td>47.37%</td></tr>
 <tr><td>Zeta Textiles IPO</td><td>₹5</td><td>₹60</td><td>8.33%</td></tr>
</table>
<table><tr><th>Upcoming IPO</th><th>GMP</th><th>Price</th><th>Gain</th></tr>
 <tr><td>Delta Pharma Limited IPO</td><td>₹240</td><td>₹1080</td><td></td></tr>
</table></body></html>
"""


@pytest.fixture
def today():
    return dt.date(2026, 10, 19)  # a Monday


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test runs in its own directory (DB, debug/, .state/) with no real
    credentials and no real sleeping or network."""
    monkeypatch.chdir(tmp_path)
    for name in ("META_WA_TOKEN", "META_WA_PHONE_NUMBER_ID", "META_WA_TO", "META_WA_TEMPLATE",
                 "CALLMEBOT_PHONE", "CALLMEBOT_APIKEY", "TWILIO_ACCOUNT_SID",
                 "TWILIO_AUTH_TOKEN", "TWILIO_TO", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                 "FALLBACK_PROVIDER"):
        monkeypatch.setattr(config, name, "")
    monkeypatch.setattr(config, "WHATSAPP_PROVIDER", "auto")
    monkeypatch.setattr("time.sleep", lambda *_: None)
    for var in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(var, raising=False)
    yield tmp_path


class FakeResponse:
    def __init__(self, status=200, json_data=None, text=""):
        self.status_code = status
        self._json = json_data if json_data is not None else {}
        self.text = text or str(self._json)

    def json(self):
        return self._json
