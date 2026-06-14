"""Tests for the Screener.in results-page parser against the real DOM shape."""
from __future__ import annotations

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bs4 import BeautifulSoup
from earnings_intel.data.screener import parse_results_soup, _parse_price_line, _num
from earnings_intel import screener_screen as ss

FIXTURE = """
<div class="mark-visited">
  <a href="/company/compare/00000/">Metals - Non Ferrous</a>
  <a href="/company/HINDZINC/">Hindustan Zinc</a>
  <p class="sub">Price <span>₹ 567</span> M.Cap <span>₹ 2,39,491 Cr</span> PE <span>17.5</span></p>
  <div class="bg-base border-radius-8 padding-small responsive-holder">
    <table class="data-table margin-0">
      <thead><tr><th></th><th>YOY</th><th>Mar 2026</th><th>Dec 2025</th><th>Mar 2025</th></tr></thead>
      <tbody>
        <tr><td>Sales</td><td>⇡ 49%</td><td>13,488</td><td>10,922</td><td>9,041</td></tr>
        <tr><td>EBIDT</td><td>⇡ 60%</td><td>7,666</td><td>6,005</td><td>4,783</td></tr>
        <tr><td>Net profit</td><td>⇡ 68%</td><td>4,997</td><td>3,879</td><td>2,976</td></tr>
        <tr><td>EPS</td><td>⇡ 68%</td><td>₹ 11.83</td><td>₹ 9.18</td><td>₹ 7.04</td></tr>
      </tbody>
    </table>
  </div>
  <a href="/company/SONAMLTD/">Sonam</a>
  <p class="sub">Price ₹ 55.7 M.Cap ₹ 223 Cr PE 30.5</p>
  <div class="responsive-holder"><table class="data-table">
    <thead><tr><th></th><th>YOY</th><th>Mar 2026</th><th>Dec 2025</th><th>Mar 2025</th></tr></thead>
    <tbody>
      <tr><td>Sales</td><td>⇡ 101%</td><td>63.6</td><td>38.1</td><td>31.7</td></tr>
      <tr><td>EBIDT</td><td>⇡ 101%</td><td>5.66</td><td>3.91</td><td>2.81</td></tr>
      <tr><td>Net profit</td><td>⇡ 71%</td><td>2.90</td><td>2.20</td><td>1.70</td></tr>
    </tbody>
  </table></div>
</div>
"""


def test_num_and_price():
    assert _num("2,39,491") == 239491.0
    assert _num("₹ None") is None
    assert _num("-0.09") == -0.09
    lp, mc, pe = _parse_price_line("Price ₹ 567 M.Cap ₹ 2,39,491 Cr PE 17.5")
    assert lp == 567 and mc == 239491 and pe == 17.5


def test_parse_results():
    rows = parse_results_soup(BeautifulSoup(FIXTURE, "lxml"))
    assert len(rows) == 2                      # compare-link ignored
    hz = rows[0]
    assert hz["code"] == "HINDZINC" and hz["name"] == "Hindustan Zinc"
    assert hz["market_cap"] == 239491.0 and hz["pe"] == 17.5
    assert abs(hz["sales_yoy"] - 49.19) < 0.1     # 13488 vs 9041
    assert abs(hz["np_yoy"] - 67.91) < 0.1        # 4997 vs 2976
    assert abs(hz["sales_qoq"] - 23.49) < 0.1     # 13488 vs 10922
    sonam = rows[1]
    assert sonam["code"] == "SONAMLTD"
    assert abs(sonam["sales_yoy"] - 100.63) < 0.1


def test_parse_then_score():
    rows = parse_results_soup(BeautifulSoup(FIXTURE, "lxml"))
    stocks = [ss.from_metrics(d) for d in rows]
    assert all(0 <= s.pead_score <= 100 for s in stocks)
    assert all(s.growth_gate for s in stocks)     # both >25% sales & NP
    ranked = ss.screen(stocks, ss.ScreenFilters(pead_min=0))
    assert ranked[0].pead_score >= ranked[-1].pead_score


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = 0
    for fn in fns:
        try:
            fn(); print(f"PASS  {fn.__name__}"); ok += 1
        except Exception as e:
            print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{ok}/{len(fns)} passed")
    return ok == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)
