import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import importlib
from earnings_intel.data import signal as sg
rt = importlib.import_module("scripts.refresh_technofunda")


def test_board_signal_buy():
    v = sg.board_signal({"profit_var": 40, "sales_var": 22, "roce": 25, "fii_chg": 0.5,
                         "pe": 22, "cmp": 960, "low_52w": 500, "ath": 1000})
    assert v["label"] == "BUY" and v["composite"] >= 70
    assert v["momentum"] >= 60 and v["results"] >= 60


def test_board_signal_sell():
    v = sg.board_signal({"profit_var": -30, "sales_var": -12, "roce": 4, "fii_chg": -0.4,
                         "pe": 120, "cmp": 55, "low_52w": 50, "ath": 300})
    assert v["label"] == "SELL" and v["composite"] <= 40


def test_board_signal_confluence_neutral():
    # great results+quality but weak momentum -> NEUTRAL, not BUY
    v = sg.board_signal({"profit_var": 30, "sales_var": 18, "roce": 22, "fii_chg": 0.2,
                         "pe": 20, "cmp": 120, "low_52w": 110, "ath": 400})
    assert v["label"] == "NEUTRAL"


def test_board_signal_handles_missing_price():
    v = sg.board_signal({"profit_var": 25, "sales_var": 15, "roce": 18})
    assert v["label"] in ("BUY", "NEUTRAL", "SELL") and v["pos_52w"] is None


# --------------------------------------------------- momentum is real data
# The board shipped with momentum == 50 on all 5,183 rows: a screen returns no
# 52-week low and no all-time high, so the range path never ran. 35% of every
# composite was one constant, `mom >= 50` was always exactly true and the SELL
# gate `mom < 35` could not fire. rs_rating is the input that was already on
# disk in every price bundle.

def test_relative_strength_drives_momentum():
    strong = sg.board_signal({"profit_var": 10, "sales_var": 5, "rs_rating": 92})
    weak = sg.board_signal({"profit_var": 10, "sales_var": 5, "rs_rating": 8})
    assert strong["momentum"] == 92 and weak["momentum"] == 8
    assert strong["composite"] > weak["composite"]


def test_two_companies_with_the_same_fundamentals_can_differ_on_momentum():
    """The regression that matters: identical rows must not score identically
    just because the momentum input went missing."""
    same = {"profit_var": 30, "sales_var": 18, "roce": 22, "pe": 20}
    assert (sg.board_signal({**same, "rs_rating": 95})["composite"]
            != sg.board_signal({**same, "rs_rating": 12})["composite"])


def test_a_weak_stock_can_now_reach_the_sell_gate_on_momentum():
    v = sg.board_signal({"profit_var": -20, "sales_var": -5, "roce": 5,
                         "rs_rating": 6})
    assert v["momentum"] < 35 and v["label"] == "SELL"


def test_a_missing_rating_is_neutral_not_zero():
    """Absent momentum must not read as the worst possible momentum."""
    v = sg.board_signal({"profit_var": 30, "sales_var": 18, "roce": 22})
    assert v["momentum"] == 50


def test_a_junk_rating_does_not_raise_or_score():
    for junk in ("high", None, "", [], {}):
        v = sg.board_signal({"profit_var": 10, "rs_rating": junk})
        assert v["momentum"] == 50, junk


def test_the_range_position_is_never_published_as_percent_of_all_time_high():
    """pos_52w means position in the 52-week range. The legacy cmp/low/high
    path computes % of ATH, a different number, and must not fill the field."""
    v = sg.board_signal({"profit_var": 10, "cmp": 960, "low_52w": 500,
                         "ath": 1000, "pos_52w": 43.5})
    assert v["pos_52w"] == 43.5


def test_the_rating_wins_over_the_legacy_range_path():
    v = sg.board_signal({"profit_var": 10, "cmp": 990, "low_52w": 500,
                         "ath": 1000, "rs_rating": 20})
    assert v["momentum"] == 20, "a near-high price overrode the real rating"


# ------------------------------------ the sector drill-down had it too
from earnings_intel.data import sectorscore as sc  # noqa: E402


def test_sector_stock_momentum_uses_relative_strength():
    """Same defect, second board: pos was None on all 5,394 constituents and
    the momentum term contributed 0, making "momentum + earnings" earnings."""
    strong = sc.stock_signal({"profit_var": 10, "sales_var": 5, "rs_rating": 100})
    weak = sc.stock_signal({"profit_var": 10, "sales_var": 5, "rs_rating": 0})
    assert strong["sscore"] > weak["sscore"]


def test_a_mid_rating_is_neutral_on_the_sector_scale():
    """50 is the index median, which must map to the centre of -1..+1, not to
    a tailwind."""
    mid = sc.stock_signal({"profit_var": 0, "sales_var": 0, "rs_rating": 50})
    none_ = sc.stock_signal({"profit_var": 0, "sales_var": 0})
    assert mid["sscore"] == none_["sscore"] == 0.0


def test_the_sector_range_column_is_populated():
    v = sc.stock_signal({"profit_var": 10, "rs_rating": 60, "pos_52w": 43.5})
    assert v["pos"] == 43.5


def test_sector_stock_signal_survives_junk():
    for junk in ("high", None, "", []):
        assert sc.stock_signal({"profit_var": 5, "rs_rating": junk})["sscore"] is not None


def test_build_row_maps_fields():
    base = {"code": "ABC", "name": "ABC Ltd", "cmp": 250, "pe": 18, "mcap": 1200,
            "sales_var": 12, "profit_var": 25, "fii_chg": 0.3,
            "low_52w": 150, "ath": 300, "roce": 20}
    r = rt.build_row(base)
    assert r["code"] == "ABC" and r["ltp"] == 250 and r["mcap"] == 1200
    assert r["sales_yoy"] == 12 and r["np_yoy"] == 25
    assert r["label"] in ("BUY", "NEUTRAL", "SELL")
    assert {"composite", "results", "momentum", "quality", "pos_52w"} <= set(r)
