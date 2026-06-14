"""Unit tests for the Fundamental Screener fetcher (earnings_intel/data/company.py)."""
import sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earnings_intel.data import company as co


class _Resp:
    def __init__(self, status=200, js=None, text=""):
        self.status_code = status; self._js = js; self.text = text
    def json(self): return self._js


def test_search_parses_code_from_url(monkeypatch):
    payload = [
        {"id": 1, "name": "Oberoi Realty Ltd", "url": "/company/OBEROIRLTY/consolidated/"},
        {"id": 2, "name": "Reliance Industries Ltd", "url": "/company/RELIANCE/"},
        {"id": 3, "name": "Bad row", "url": "/nope/"},
    ]
    class S:
        headers = {}
        def get(self, *a, **k): return _Resp(200, js=payload)
        cookies = types.SimpleNamespace(set=lambda *a, **k: None)
    monkeypatch.setattr(co, "_client", lambda sid: S())
    out = co.search("ob", "sid")
    assert [(o["name"], o["code"]) for o in out] == [
        ("Oberoi Realty Ltd", "OBEROIRLTY"), ("Reliance Industries Ltd", "RELIANCE")]


def test_search_empty_query_no_network():
    assert co.search("", "sid") == []


def test_fundamentals_extracts_sections(monkeypatch):
    html = """
    <h1>Oberoi Realty Ltd</h1>
    <ul id="top-ratios">
      <li><span class="name">Market Cap</span><span class="value">₹ 59,391 Cr.</span></li>
      <li><span class="name">Current Price</span><span class="value">₹ 1,633</span></li>
      <li><span class="name">Stock P/E</span><span class="value">31.0</span></li>
    </ul>
    <table class="ranges-table"><tr><th>Compounded Sales Growth</th></tr>
      <tr><td>10 Years:</td><td>15%</td></tr><tr><td>5 Years:</td><td>42%</td></tr></table>
    <section id="quarters"><table>
      <thead><tr><th>Particulars</th><th>Dec 2025</th><th>Mar 2026</th></tr></thead>
      <tbody>
        <tr><td>Sales&nbsp;+</td><td>1,180</td><td>1,415</td></tr>
        <tr><td>OPM %</td><td>50%</td><td>52%</td></tr>
        <tr><td>Net Profit&nbsp;+</td><td>485</td><td>560</td></tr>
        <tr><td>EPS in Rs</td><td>13.3</td><td>15.4</td></tr>
      </tbody></table></section>
    <div class="pros"><ul><li>Low debt</li></ul></div>
    <div class="cons"><ul><li>High valuation</li></ul></div>
    """
    class S:
        headers = {}
        def get(self, *a, **k): return _Resp(200, text=html)
        cookies = types.SimpleNamespace(set=lambda *a, **k: None)
    monkeypatch.setattr(co, "_client", lambda sid: S())
    f = co.fundamentals("OBEROIRLTY", "sid")
    assert f["name"] == "Oberoi Realty Ltd"
    assert f["overview"]["Market Cap"] == "₹ 59,391 Cr."
    assert f["overview"]["Stock P/E"] == "31.0"
    assert f["growth"]["Compounded Sales Growth"]["10 Years"] == "15%"
    assert f["quarters"]["headers"] == ["Dec 2025", "Mar 2026"]
    assert f["quarters"]["rows"]["Sales"] == ["1,180", "1,415"]
    assert f["quarters"]["rows"]["Net Profit"] == ["485", "560"]
    assert f["quarters"]["rows"]["EPS"] == ["13.3", "15.4"]
    assert f["pros"] == ["Low debt"]
    assert f["cons"] == ["High valuation"]


def test_fundamentals_http_error(monkeypatch):
    class S:
        headers = {}
        def get(self, *a, **k): return _Resp(503, text="")
        cookies = types.SimpleNamespace(set=lambda *a, **k: None)
    monkeypatch.setattr(co, "_client", lambda sid: S())
    assert co.fundamentals("X", "sid")["error"] == "http 503"


def test_fundamentals_includes_statements_and_analysis(monkeypatch):
    import types
    html = """
    <h1>Demo Ltd</h1>
    <ul id="top-ratios">
      <li><span class="name">Market Cap</span><span class="value">₹ 1,000 Cr.</span></li>
      <li><span class="name">Current Price</span><span class="value">₹ 200</span></li>
      <li><span class="name">Stock P/E</span><span class="value">20.0</span></li>
    </ul>
    <table class="ranges-table"><tr><th>Compounded Profit Growth</th></tr>
      <tr><td>5 Years:</td><td>18%</td></tr></table>
    <table class="ranges-table"><tr><th>Stock Price CAGR</th></tr>
      <tr><td>5 Years:</td><td>15%</td></tr></table>
    <section id="quarters"><table><thead><tr><th>x</th><th>Dec 2025</th><th>Mar 2026</th></tr></thead>
      <tbody><tr><td>Sales</td><td>100</td><td>120</td></tr><tr><td>OPM %</td><td>20%</td><td>22%</td></tr>
      <tr><td>Net Profit</td><td>40</td><td>50</td></tr><tr><td>EPS in Rs</td><td>4</td><td>5</td></tr></tbody></table></section>
    <section id="profit-loss"><table><thead><tr><th>x</th><th>Mar 2025</th><th>Mar 2026</th></tr></thead>
      <tbody><tr><td>Sales+</td><td>400</td><td>500</td></tr><tr><td>OPM %</td><td>20%</td><td>21%</td></tr>
      <tr><td>Net Profit+</td><td>45</td><td>50</td></tr><tr><td>EPS in Rs</td><td>9</td><td>10</td></tr></tbody></table></section>
    <section id="balance-sheet"><table><thead><tr><th>x</th><th>Mar 2025</th><th>Mar 2026</th></tr></thead>
      <tbody><tr><td>Reserves+</td><td>300</td><td>350</td></tr></tbody></table></section>
    <section id="cash-flow"><table><thead><tr><th>x</th><th>Mar 2025</th><th>Mar 2026</th></tr></thead>
      <tbody><tr><td>Cash from Operating Activity+</td><td>60</td><td>70</td></tr>
      <tr><td>Net Cash Flow</td><td>5</td><td>8</td></tr></tbody></table></section>
    <section id="ratios"><table><thead><tr><th>x</th><th>Mar 2025</th><th>Mar 2026</th></tr></thead>
      <tbody><tr><td>ROCE %</td><td>18%</td><td>20%</td></tr></tbody></table></section>
    <section id="shareholding"><table><thead><tr><th>x</th><th>Dec 2025</th><th>Mar 2026</th></tr></thead>
      <tbody><tr><td>Promoters+</td><td>55%</td><td>56%</td></tr><tr><td>FIIs+</td><td>10%</td><td>12%</td></tr>
      <tr><td>DIIs+</td><td>8%</td><td>9%</td></tr></tbody></table></section>
    """
    class S:
        headers = {}
        def get(self, *a, **k): return _Resp(200, text=html)
        cookies = types.SimpleNamespace(set=lambda *a, **k: None)
    monkeypatch.setattr(co, "_client", lambda sid: S())
    f = co.fundamentals("DEMO", "sid")
    assert f["profit_loss"]["rows"]["Sales"] == ["400", "500"]
    assert f["balance_sheet"]["rows"]["Reserves"] == ["300", "350"]
    assert f["cash_flow"]["rows"]["Net Cash Flow"] == ["5", "8"]
    assert f["ratios"]["rows"]["ROCE %"] == ["18%", "20%"]
    assert f["shareholding"]["rows"]["Promoters"] == ["55%", "56%"]
    an = f["analysis"]
    assert an["dcf"]["ok"] is True
    assert "Sales" in an["trends"]["yearly"]
    assert an["money_flow"]["label"] == "POSITIVE MONEY FLOW"
