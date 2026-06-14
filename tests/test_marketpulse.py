import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bs4 import BeautifulSoup
from earnings_intel.data import marketpulse as mp


FII_HTML = """
<article class="box"><div class="box-header">
  <div><div class="box-title"><a href="/market/IN05/IN0501/">Financial Services</a></div>
  <div class="sub">27.0% of AUM</div></div>
  <div class="text-align-right"><span class="badge badge-delta badge-delta-down">▼ -4,978 Cr</span>
  <div class="sub">Last fortnight</div></div></div>
  <div class="text-align-center"><div>₹ -1,45,774 Cr</div><div class="sub">1Y net flow</div></div>
</article>
<article class="box"><div class="box-header">
  <div><div class="box-title"><a href="/market/IN07/IN0702/">Capital Goods</a></div>
  <div class="sub">6.9% of AUM</div></div>
  <div class="text-align-right"><span class="badge badge-delta badge-delta-up">▲ + 154 Cr</span>
  <div class="sub">Last fortnight</div></div></div>
  <div class="text-align-center"><div>₹ 30,305 Cr</div><div class="sub">1Y net flow</div></div>
</article>
"""

TRADES_HTML = """
<table><thead><tr><th>Company</th><th>Person</th><th>Date</th><th>Type</th><th>Value</th></tr></thead>
<tbody>
<tr><td><a href="/company/SHANTIGOLD/">Shanti Gold</a></td><td>Arihant Capital Markets Limited</td><td>08 Jun 2026</td><td>Sell</td><td>11.69 crore 5,13,558</td></tr>
<tr><td><a href="/company/SHANTIGOLD/">Shanti Gold</a></td><td>Graviton Research</td><td>08 Jun 2026</td><td>Buy</td><td>13.23 crore 5,84,957</td></tr>
</tbody></table>
"""

DIV_HTML = """
<table><thead><tr><th>Company</th><th>Ex date</th><th>Div type</th><th>Percent</th></tr></thead>
<tbody>
<tr><td><a href="/company/ANDHRSUGAR/">Andhra Sugars</a></td><td>19 September 2026</td><td>Special</td><td>10.00</td></tr>
</tbody></table>
"""

ANN_HTML = """
<ul><li class="ann"><a href="/company/GPECO/">GP Eco Solutions</a>
   <span>Committee Meeting Intimation</span> <span>Today</span></li>
<li class="ann"><a href="/company/CIPLA/">Cipla</a>
   <span>Notice Of 90th AGM</span> <span>09 Jun 2026</span></li></ul>
"""


def test_parse_fii():
    rows = mp.parse_fii(BeautifulSoup(FII_HTML, "lxml"))
    assert len(rows) == 2
    fs = rows[0]
    assert fs["sector"] == "Financial Services" and fs["code"] == "IN0501"
    assert fs["aum"] == 27.0
    assert fs["fortnight"] == -4978.0
    assert fs["oneY"] == -145774.0
    cg = rows[1]
    assert cg["code"] == "IN0702" and cg["fortnight"] == 154.0 and cg["oneY"] == 30305.0


def test_parse_trades():
    rows = mp.parse_trades(BeautifulSoup(TRADES_HTML, "lxml"), "bulk")
    assert len(rows) == 2
    assert rows[0]["deal"] == "bulk"
    assert rows[0]["company"] == "Shanti Gold" and rows[0]["code"] == "SHANTIGOLD"
    assert rows[0]["type"] == "Sell" and abs(rows[0]["value_cr"] - 11.69) < 0.01
    assert rows[0]["qty"] == "5,13,558"
    assert rows[1]["type"] == "Buy"


def test_parse_actions_dividend():
    rows = mp.parse_actions(BeautifulSoup(DIV_HTML, "lxml"), "dividend")
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "dividend" and r["company"] == "Andhra Sugars"
    assert r["code"] == "ANDHRSUGAR" and r["ex_date"] == "19 September 2026"
    assert "Special" in r["detail"] and "10.00" in r["detail"]


def test_parse_announcements():
    rows = mp.parse_announcements(BeautifulSoup(ANN_HTML, "lxml"))
    assert len(rows) >= 2
    gp = next(r for r in rows if r["code"] == "GPECO")
    assert gp["company"] == "GP Eco Solutions"
    assert "Committee Meeting" in gp["title"]
    assert gp["when"] == "Today"


from earnings_intel.data import sectorscore as sc


def test_blend_fii_injects_real_flow_and_reranks():
    result = {"full_market": {"score": 0.0, "signal": "NEUTRAL"}, "sectors": [
        {"sector": "Metals & Mining", "sector_code": "IN0103", "score": 0.1, "signal": "NEUTRAL",
         "mcap": 1000, "components": {"momentum": 0.2, "strength": 0.0, "flow": 0.0, "quality": 0.0}},
        {"sector": "Financial Services", "sector_code": "IN0501", "score": 0.1, "signal": "NEUTRAL",
         "mcap": 2000, "components": {"momentum": 0.2, "strength": 0.0, "flow": 0.0, "quality": 0.0}}]}
    fii = [{"sector": "Metals & Mining", "code": "IN0103", "fortnight": 4999, "oneY": 50000, "aum": 4.0},
           {"sector": "Financial Services", "code": "IN0501", "fortnight": -4978, "oneY": -145774, "aum": 27.0}]
    out = sc.blend_fii(result, fii)
    m = next(s for s in out["sectors"] if s["sector_code"] == "IN0103")
    f = next(s for s in out["sectors"] if s["sector_code"] == "IN0501")
    assert m["components"]["flow"] > 0 and m["fii_fortnight"] == 4999
    assert f["components"]["flow"] < 0 and f["fii_1y"] == -145774
    assert m["score"] > f["score"]          # inflow sector now outranks outflow sector


def test_blend_fii_joins_by_normalised_name():
    r = {"full_market": {"score": 0.0, "signal": "NEUTRAL"}, "sectors": [
        {"sector": "Automobile & Auto Components", "sector_code": None, "score": 0.1, "mcap": 10,
         "components": {"momentum": 0.2, "strength": 0.0, "flow": 0.0, "quality": 0.0}}]}
    sc.blend_fii(r, [{"sector": "Automobile and Auto Components", "code": "IN0201",
                      "fortnight": -2197, "oneY": -18414, "aum": 6.8}])
    assert r["sectors"][0]["components"]["flow"] < 0 and r["sectors"][0]["fii_fortnight"] == -2197
