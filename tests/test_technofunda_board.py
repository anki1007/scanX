import sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import importlib
rt = importlib.import_module("scripts.refresh_technofunda")


def test_score_one_builds_row(monkeypatch):
    fund = {"name": "Demo Ltd",
            "overview": {"Market Cap": "₹ 1,200 Cr.", "Stock P/E": "18"},
            "analysis": {"dcf": {"margin_of_safety": 20}}}
    price = {"ticker": "DEMO.NS", "technical": {"price": 250, "rs_rating": 70}}
    verdict = {"label": "BUY", "composite": 78, "confidence": "High",
               "blocks": {"results": {"score": 80}, "technical": {"score": 75},
                          "fundamental": {"score": 79}}}
    monkeypatch.setattr(rt.co, "fundamentals", lambda code, sid: fund)
    monkeypatch.setattr(rt.ph, "price_analytics", lambda code, overview=None: price)
    monkeypatch.setattr(rt.sg, "technofunda_signal", lambda f, p: verdict)
    base = {"code": "DEMO", "name": "Demo Ltd", "cmp": 248,
            "mcap": 1200, "pe": 18, "sales_var": 12, "profit_var": 25}
    r = rt.score_one(base, "sid")
    assert r["label"] == "BUY" and r["composite"] == 78
    assert r["results"] == 80 and r["technical"] == 75 and r["fundamental"] == 79
    assert r["ltp"] == 250 and r["rs_rating"] == 70
    assert r["mcap"] == 1200.0 and r["pe"] == 18.0
    assert r["sales_yoy"] == 12 and r["np_yoy"] == 25


def test_score_one_skips_errors(monkeypatch):
    monkeypatch.setattr(rt.co, "fundamentals", lambda code, sid: {"error": "http 404"})
    assert rt.score_one({"code": "X"}, "sid") is None
