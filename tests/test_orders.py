"""Tests for the Orders tab: text extraction, fundamentals DOM parse, aggregation."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from earnings_intel.data import orders as o          # noqa: E402
import refresh_orders as ro                          # noqa: E402

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None


def test_value_cr():
    assert o.parse_value_cr("Order worth Rs. 1,734 crore") == 1734.0
    assert o.parse_value_cr("received order of ₹1734 Cr") == 1734.0
    assert o.parse_value_cr("bagged a 250 crore order") == 250.0
    assert o.parse_value_cr("contract of Rs 50 lakh") == 0.5
    assert o.parse_value_cr("no value mentioned") is None


def test_duration():
    assert o.parse_duration("to be executed in 12 months") == "12 months"
    assert o.parse_duration("over 3 years") == "3 years"
    assert o.parse_duration("immediately") is None


def test_customer():
    assert o.parse_customer("received order from Indian Railways for supply") == "Indian Railways"
    assert o.parse_customer("order worth 100 crore received") is None


def test_order_type():
    assert o.parse_order_type("Receipt of Purchase Order") == "Purchase Order"
    assert o.parse_order_type("Company bagged a contract") == "Contract"
    assert o.parse_order_type("Letter of Award received") == "Letter of Award"


def test_looks_like_order():
    assert o.looks_like_order("Award of Order / Receipt of Order", "intimation")
    assert not o.looks_like_order("Board Meeting", "quarterly results declared")


def test_order_size_pct():
    assert o.order_size_pct(1734.0, 1149.0) == 150.91
    assert o.order_size_pct(None, 100) is None
    assert o.order_size_pct(10, None) is None


def test_consolidate():
    rows = [
        {"code": "500", "name": "A", "value_cr": 100, "revenue_fy": 50, "market_cap": 200},
        {"code": "500", "name": "A", "value_cr": 50, "revenue_fy": 50, "market_cap": 200},
        {"code": "600", "name": "B", "value_cr": 20, "revenue_fy": 40, "market_cap": 80},
    ]
    c = ro.consolidate(rows)
    a = next(x for x in c if x["code"] == "500")
    assert a["order_count"] == 2 and a["total_value_cr"] == 150
    assert a["orders_pct_revenue"] == 300.0
    assert c[0]["code"] == "500"   # highest % first


def test_screener_fundamentals_parse():
    if BeautifulSoup is None:
        return
    html = """
    <div id="top-ratios"><li><span class="name">Market Cap</span>
      <span class="value">₹ <span class="number">1,149</span> Cr</span></li></div>
    <section id="quarters"><table>
      <thead><tr><th></th><th>Jun 2025</th><th>Sep 2025</th></tr></thead>
      <tbody>
        <tr><td>Sales&nbsp;+</td><td>100</td><td>120</td></tr>
        <tr><td>OPM %</td><td>20</td><td>25</td></tr>
        <tr><td>Net Profit&nbsp;+</td><td>10</td><td>15</td></tr>
        <tr><td>EPS in Rs</td><td>2</td><td>3</td></tr>
      </tbody></table></section>
    <section id="profit-loss"><table><tbody>
        <tr><td>Sales&nbsp;+</td><td>300</td><td>400</td><td>500</td></tr>
    </tbody></table></section>
    """
    f = o.CompanyFundamentals(code="X")
    o.ScreenerFundamentals._parse(BeautifulSoup(html, "lxml"), f)
    assert f.market_cap == 1149
    assert f.sales_latest_q == 120 and f.opm_latest == 25
    assert f.np_latest_q == 15 and f.np_prev_q == 10 and f.np_growth_qoq == 50.0
    assert f.eps_latest_q == 3 and f.eps_prev_q == 2 and f.eps_growth_qoq == 50.0
    assert f.revenue_fy == 500
