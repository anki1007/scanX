import sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest
pytest.importorskip("bs4")
from bs4 import BeautifulSoup
from earnings_intel.data import screener as sc
from earnings_intel.data import signal as sg

SCREEN_HTML = """
<table class="data-table">
 <tr><th>S.No.</th><th>Name</th><th>CMP Rs.</th><th>P/E</th><th>52w Low Rs.</th>
     <th>Mar Cap Rs.Cr.</th><th>Qtr Profit Var %</th><th>Sales Qtr Rs.Cr.</th>
     <th>Qtr Sales Var %</th><th>Chg in FII Hold %</th><th>All time high Rs.</th><th>ROCE %</th></tr>
 <tr><td>1.</td><td><a href="/company/RELIANCE/consolidated/">Reliance</a></td>
     <td>1,291</td><td>40.9</td><td>1,100</td><td>17,47,050</td><td>24.4</td>
     <td>2,40,000</td><td>7.1</td><td>0.5</td><td>1,600</td><td>10.2</td></tr>
 <tr><td>2.</td><td><a href="/company/500325/">RIL B</a></td>
     <td>50</td><td>12</td><td>40</td><td>900</td><td>-30</td>
     <td>100</td><td>-12</td><td>-0.2</td><td>120</td><td>5</td></tr>
</table>"""


def test_screen_colmap_and_parse(monkeypatch):
    c = sc.ScreenerClient.__new__(sc.ScreenerClient)  # bypass __init__
    c.delay = 0
    soups = [BeautifulSoup(SCREEN_HTML, "lxml"), None]
    monkeypatch.setattr(c, "_get", lambda url: soups.pop(0))
    rows = c.fetch_screen("Market Capitalization > 100", max_pages=2)
    assert len(rows) == 2
    r = rows[0]
    assert r["code"] == "RELIANCE" and r["mcap"] == 1747050.0
    assert r["pe"] == 40.9 and r["profit_var"] == 24.4 and r["sales_var"] == 7.1
    assert r["fii_chg"] == 0.5 and r["roce"] == 10.2
    assert rows[1]["code"] == "500325"   # numeric BSE code parsed too


def test_prescreen_orders_candidates():
    strong = sg.prescreen_score({"profit_var": 40, "sales_var": 20, "roce": 22, "fii_chg": 0.4, "pe": 18})
    weak = sg.prescreen_score({"profit_var": -25, "sales_var": -10, "roce": 4, "fii_chg": -0.5, "pe": 90})
    assert strong > 80 and weak < 35 and strong > weak
