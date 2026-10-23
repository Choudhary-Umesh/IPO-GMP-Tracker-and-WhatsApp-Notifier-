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


# Shape of ipowatch.live's GetIpoGMPHistory response (captured Oct 2026).
IW_API = {
    "status": True, "message": "", "jwtToken": None, "totalCount": 4, "id": 0,
    "result": [
        {"id": 1, "ipoName": "Alpha Cement", "ipo_slug": "alpha-cement-ipo", "ipoGmp": "₹54",
         "priceBand": "₹100 - ₹114", "estListing": "₹168 (47.37%)", "IPODate": "17-19 October",
         "Type": "BSE BSE SME", "Status": "Open", "LastUpdated": "19 October, 12:10",
         "trendIcon": "fa-arrow-up", "trendColor": "#28A745"},
        {"id": 2, "ipoName": "Delta Pharma", "ipo_slug": "delta-pharma-ipo", "ipoGmp": "₹240",
         "priceBand": "₹1028 - ₹1080", "estListing": "₹1320 (22.22%)", "IPODate": "15-19 October",
         "Type": "Mainboard", "Status": "Open", "LastUpdated": "19 October, 11:05",
         "trendIcon": "fa-arrow-right", "trendColor": "#FFC107"},
        {"id": 3, "ipoName": "Zeta Textiles", "ipo_slug": "zeta-textiles-ipo", "ipoGmp": "₹0",
         "priceBand": "₹55 - ₹60", "estListing": "₹- (-%)", "IPODate": "27-29 October",
         "Type": "Mainboard", "Status": "Upcoming", "LastUpdated": "19 October, 10:00",
         "trendIcon": "fa-arrow-right", "trendColor": "#FFC107"},
        {"id": 4, "ipoName": "", "ipoGmp": "₹5"},
    ],
}


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
                 "TELEGRAM_EXTRA_CHAT_IDS",
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
