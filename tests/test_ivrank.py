import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from earnings_intel.data import ivrank as iv


def test_iv_metrics_from_fund():
    fund = {"code": "X", "name": "X Ltd",
            "overview": {"Current Price": "₹ 386", "Book Value": "₹ 173",
                         "Market Cap": "₹ 32,802 Cr.", "ROCE": "15 %"},
            "balance_sheet": {"rows": {"Borrowings": ["100", "200"]}},
            "profit_loss": {"rows": {"Operating Profit": ["2,000", "3,000"]}},
            "growth": {"Compounded Sales Growth": {"3 Years": "6%"},
                       "Compounded Profit Growth": {"3 Years": "9%"}}}
    m = iv.iv_metrics(fund)
    assert m["pb"] == round(386 / 173, 2)
    assert m["ev_ebitda"] == round((32802 + 200) / 3000, 2)
    assert m["sales_3y"] == 6 and m["profit_3y"] == 9 and m["roce"] == 15


def test_rank_funnel_orders_low_total_first():
    stocks = [
        {"code": "A", "sales_3y": 30, "roce": 30, "pb": 1.0, "mcap": 500, "sector": "S"},  # best both
        {"code": "B", "sales_3y": 5, "roce": 5, "pb": 9.0, "mcap": 500, "sector": "S"},    # worst both
        {"code": "C", "sales_3y": 20, "roce": 20, "pb": 3.0, "mcap": 500, "sector": "S"}]
    r = iv.rank_funnel(stocks)
    assert r[0]["code"] == "A" and r[-1]["code"] == "B"
    assert r[0]["funnel_rank"] == 2   # rank1 gq + rank1 pb


def test_rank_magic_orders():
    stocks = [
        {"code": "A", "roce": 40, "ev_ebitda": 4},   # high roce, low ev -> best
        {"code": "B", "roce": 8, "ev_ebitda": 30},
        {"code": "C", "roce": 20, "ev_ebitda": 12}]
    r = iv.rank_magic(stocks)
    assert r[0]["code"] == "A" and r[-1]["code"] == "B"


def test_top_per_sector_caps_and_floor():
    ranked = [{"code": str(i), "funnel_rank": i, "sector": "S", "mcap": 500} for i in range(1, 6)]
    ranked += [{"code": "small", "funnel_rank": 0, "sector": "S", "mcap": 50}]  # below floor
    top = iv.top_per_sector(ranked, "funnel_rank", n=3, mcap_floor=200)
    assert len(top) == 3 and "small" not in [t["code"] for t in top]
