import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from earnings_intel.data import analytics as A


def test_to_float():
    assert A.to_float("₹ 1,234 Cr.") == 1234.0
    assert A.to_float("-2%") == -2.0
    assert A.to_float("") is None
    assert A.to_float(None) is None


def test_trend_increasing_decreasing_inconsistent():
    assert A.trend_of([1, 2, 3, 4, 5])["label"] == "Increasing"
    assert A.trend_of([5, 4, 3, 2, 1])["label"] == "Decreasing"
    assert A.trend_of([1, 3, 2, 4, 3])["label"] == "Inconsistent"
    assert A.trend_of([7])["label"] == "n/a"


def test_classify_trends_shape():
    out = A.classify_trends({"Sales": [1, 2, 3, 4]}, {"EPS": [4, 3, 2, 1]})
    assert out["yearly"]["Sales"]["label"] == "Increasing"
    assert out["quarterly"]["EPS"]["label"] == "Decreasing"
    assert out["quarterly"]["EPS"]["unit"] == "qtrs"


def test_cyclical_detects_positive_month():
    headers = ["Mar 2023", "Jun 2023", "Sep 2023", "Dec 2023",
               "Mar 2024", "Jun 2024", "Sep 2024", "Dec 2024"]
    # Dec always jumps vs Sep -> Dec positive
    profit = [10, 8, 9, 20, 11, 9, 10, 22]
    c = A.cyclical(headers, profit)
    assert "Dec" in c["positive_quarters"]
    assert c["label"] in ("CYCLICAL", "NON-CYCLICAL")


def test_money_flow_direction():
    pos = A.money_flow(["a", "b"], ["10", "11"], ["5", "6"])
    assert pos["label"] == "POSITIVE MONEY FLOW" and pos["change"] == 2.0
    neg = A.money_flow(["a", "b"], ["11", "10"], ["6", "5"])
    assert neg["label"] == "NEGATIVE MONEY FLOW"


def test_dcf_math():
    d = A.dcf(100, 0, 10, 2, 10, 4)
    # flat earnings 100, 10% discount: PV1 ~ 90.9
    assert d["rows"][0]["pv"] == round(100 / 1.1)
    assert d["pv_1_n"] > 0 and d["total_pv"] > d["pv_1_n"]


def test_reverse_dcf_recovers_growth():
    # build a market cap from a known growth, then recover it
    g = 12.0
    mc = A.dcf(50, g, 10, 2, 10, 15)["total_pv"]
    rev = A.reverse_dcf(mc, 50, 10, 2, 10, 15)
    assert abs(rev["implied_growth"] - g) < 0.5


def test_auto_dcf_end_to_end():
    ov = {"Market Cap": "₹ 1000 Cr", "Current Price": "₹ 200", "Stock P/E": "20"}
    gr = {"Compounded Profit Growth": {"5 Years": "18%", "3 Years": "12%"}}
    pl = {"rows": {"Net Profit": ["40", "45", "50"]}}
    a = A.auto_dcf(ov, gr, pl)
    assert a["ok"] and a["inputs"]["earnings"] == 50
    assert a["inputs"]["growth"] == 18.0          # 5Y CAGR capped at 18
    assert a["inputs"]["terminal_multiple"] == 12.8  # Gordon (1+tg)/(r-tg)
    assert a["margin_of_safety"] is not None
