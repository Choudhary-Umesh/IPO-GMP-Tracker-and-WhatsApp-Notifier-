import datetime as dt, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "test_ipo.db")

from src import db, investorgain_scraper as ig, ipowatch_scraper as iw
from src.formatter import build_message

TODAY = dt.date.today()
d = lambda x: x.strftime("%d-%b")

IG_HTML = f"""
<html><body>
<table id="nav"><tr><th>Menu</th></tr><tr><td>Home</td></tr></table>
<table id="mainTable">
<thead><tr>
 <th>IPO</th><th>Price</th><th>GMP</th><th>Est Listing</th>
 <th>IPO Size</th><th>Lot</th><th>Open</th><th>Close</th><th>Listing</th>
</tr></thead>
<tbody>
 <tr><td>Alpha Cement Industries Ltd IPO (SME)</td><td>₹108 to ₹114</td><td>₹45</td>
     <td>₹159 (39.47%)</td><td>₹50 Cr</td><td>1200</td><td>{d(TODAY - dt.timedelta(days=2))}</td>
     <td>{d(TODAY)}</td><td>{d(TODAY + dt.timedelta(days=4))}</td></tr>
 <tr><td>Beta Logistics Limited IPO</td><td>₹200</td><td>₹10</td>
     <td>₹210 (5.00%)</td><td>₹120 Cr</td><td>75</td><td>{d(TODAY - dt.timedelta(days=1))}</td>
     <td>{d(TODAY)}</td><td>{d(TODAY + dt.timedelta(days=5))}</td></tr>
 <tr><td>Gamma Foods &amp; Beverages IPO</td><td>₹90 to ₹95</td><td>₹30</td>
     <td>₹125</td><td>₹30 Cr</td><td>1600</td><td>{d(TODAY)}</td>
     <td>{d(TODAY + dt.timedelta(days=2))}</td><td>-</td></tr>
 <tr><td>Delta Pharma Ltd IPO (SME)</td><td>₹1,080</td><td>₹250</td>
     <td>₹1,330</td><td>₹75 Cr</td><td>100</td><td>{d(TODAY - dt.timedelta(days=3))}</td>
     <td>{d(TODAY)}</td><td>-</td></tr>
</tbody></table></body></html>
"""

IW_HTML = f"""
<html><body>
<table><tr><th>Current IPO</th><th>IPO GMP</th><th>Price</th><th>Listing Gain</th></tr>
 <tr><td>Alpha Cement IPO</td><td>₹44</td><td>₹114</td><td>38.60%</td></tr>
 <tr><td>Zeta Textiles IPO</td><td>₹5</td><td>₹60</td><td>8.33%</td></tr>
</table>
<table><tr><th>Upcoming IPO</th><th>GMP</th><th>Price</th><th>Gain</th></tr>
 <tr><td>Delta Pharma Limited IPO</td><td>₹240</td><td>₹1080</td><td></td></tr>
</table></body></html>
"""

print("=== STEP 1 ===")
rows = ig.parse_rows(IG_HTML)
for r in rows:
    print(f"  {r['name']:<38} price={r['issue_price']} gmp={r['ig_gmp']} pct={r['ig_gmp_pct']} close={r['close_date']}")
matches = ig.filter_rows(rows, target_date=TODAY)
print("filtered:", [m["name"] for m in matches])

db.init_db()
db.save_candidates(matches)

print("\n=== STEP 2 ===")
entries = iw.parse_entries(IW_HTML)
for e in entries:
    print("  ", e["name"], e["gmp"], e["gmp_pct"], "|norm:", e["normalized_name"])

for ipo_id, name, norm in db.get_candidate_names():
    m, score = iw.best_match(norm, entries)
    print(f"  {name} -> {(m or {}).get('name')} ({score:.1f})")
    db.update_ipowatch(ipo_id, iw_name=m["name"] if m else None,
                       iw_gmp=m["gmp"] if m else None,
                       iw_gmp_pct=m["gmp_pct"] if m else None, score=score)

print("\n=== STEP 3 ===")
print(build_message(db.get_candidates(), TODAY))
print("\n=== EMPTY DAY ===")
print(build_message([], TODAY))
