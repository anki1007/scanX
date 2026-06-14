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
    assert v["label"] in ("BUY", "NEUTRAL", "SELL") and v["pos_ath"] is None


def test_build_row_maps_fields():
    base = {"code": "ABC", "name": "ABC Ltd", "cmp": 250, "pe": 18, "mcap": 1200,
            "sales_var": 12, "profit_var": 25, "fii_chg": 0.3,
            "low_52w": 150, "ath": 300, "roce": 20}
    r = rt.build_row(base)
    assert r["code"] == "ABC" and r["ltp"] == 250 and r["mcap"] == 1200
    assert r["sales_yoy"] == 12 and r["np_yoy"] == 25
    assert r["label"] in ("BUY", "NEUTRAL", "SELL")
    assert {"composite", "results", "momentum", "quality", "pos_ath"} <= set(r)
