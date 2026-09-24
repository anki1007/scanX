import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from earnings_intel.data import sectorscore as ss


def _stock(sector, pv, sv, roce, fii, cmp, lo, ath, mcap=1000):
    return {"sector": sector, "profit_var": pv, "sales_var": sv, "roce": roce,
            "fii_chg": fii, "cmp": cmp, "low_52w": lo, "ath": ath, "mcap": mcap}


def test_tailwind_vs_headwind():
    hot = [_stock("Tech", 40, 25, 24, 0.6, 95, 50, 100) for _ in range(5)]
    cold = [_stock("Mining", -25, -10, 5, -0.5, 52, 50, 200) for _ in range(5)]
    sh = ss.sector_score(hot); sc = ss.sector_score(cold)
    assert sh["signal"] == "TAILWIND" and sh["score"] > 0.5
    assert sc["signal"] == "HEADWIND" and sc["score"] < -0.5


def test_market_tailwind_groups_and_weights():
    rows = ([_stock("Tech", 40, 25, 24, 0.6, 95, 50, 100, mcap=5000) for _ in range(3)] +
            [_stock("Mining", -25, -10, 5, -0.5, 52, 50, 200, mcap=500) for _ in range(3)])
    out = ss.market_tailwind(rows)
    names = [s["sector"] for s in out["sectors"]]
    assert names[0] == "Tech"                      # ranked best-first
    assert out["full_market"]["score"] > 0         # weighted toward big Tech caps
    assert out["full_market"]["companies"] == 6 and out["full_market"]["sectors"] == 2


def test_handles_missing_fields():
    rows = [_stock("X", None, None, None, None, None, None, None)]
    s = ss.sector_score(rows)
    assert s["signal"] in ("TAILWIND", "NEUTRAL", "HEADWIND")


# ------------------------------------------------ breadth is a real input
# breadth_pct was null for all 22 sectors and the strength component was 0.0
# everywhere: the formula wanted low_52w and ath, and no page the scraper reads
# supplies either. 35% of every sector score was a constant. pos_52w, the
# 52-week position from the bundles' own price history, is what the board's
# legend says breadth means.

def _p52(sector, pos, pv=10, sv=10):
    return {"sector": sector, "profit_var": pv, "sales_var": sv, "roce": 15,
            "pos_52w": pos, "mcap": 1000}


def test_breadth_is_computed_from_the_52_week_position():
    rows = [_p52("X", p) for p in (90, 80, 70, 20, 10)]     # 3 of 5 upper half
    s = ss.sector_score(rows)
    assert s["breadth_pct"] == 60.0
    assert s["components"]["strength"] != 0.0


def test_two_sectors_that_differ_only_in_breadth_now_score_differently():
    strong = ss.sector_score([_p52("A", 95) for _ in range(6)])
    weak = ss.sector_score([_p52("B", 5) for _ in range(6)])
    assert strong["score"] > weak["score"]


def test_the_range_formula_still_works_when_a_page_supplies_it():
    s = ss.sector_score([_stock("Tech", 40, 25, 24, 0.6, 95, 50, 100) for _ in range(4)])
    assert s["breadth_pct"] == 100.0


def test_a_bad_position_is_ignored_not_scored():
    rows = [_p52("X", "n/a"), _p52("X", 250), _p52("X", 80)]
    assert ss.sector_score(rows)["breadth_pct"] == 100.0


def test_no_price_data_leaves_breadth_absent_not_zero():
    s = ss.sector_score([_p52("X", None) for _ in range(3)])
    assert s["breadth_pct"] is None and s["components"]["strength"] == 0.0
