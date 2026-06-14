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
