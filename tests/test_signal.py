import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from earnings_intel.data import signal as sg


def _fund(np_q, sales_q, eps_q, opm_q, *, sales_tr="Increasing", np_tr="Increasing",
          eps_tr="Increasing", roe="18 %", roce="16 %", mos=20, mf="POSITIVE MONEY FLOW"):
    return {
        "overview": {"ROE": roe, "ROCE": roce},
        "quarters": {"rows": {"Sales": sales_q, "Net Profit": np_q, "EPS": eps_q, "OPM": opm_q}},
        "analysis": {
            "trends": {"yearly": {"Sales": {"label": sales_tr}, "Net Profit": {"label": np_tr},
                                  "EPS": {"label": eps_tr}}},
            "dcf": {"margin_of_safety": mos},
            "money_flow": {"label": mf},
        },
    }


# 8-quarter series so YoY (lag 4) and acceleration (needs 6) compute
STRONG = _fund([60,65,70,75,82,92,102,120], [120,130,140,150,165,180,195,230],
               [6,6.5,7,7.5,8.2,9.2,10.2,12], [18,19,20,20,21,22,23,24])
WEAK = _fund([150,140,135,125,118,108,95,80], [260,250,245,235,225,210,195,180],
             [15,14,13.5,12.5,11.8,10.8,9.5,8], [24,23,22,21,20,19,18,17],
             sales_tr="Decreasing", np_tr="Decreasing", eps_tr="Inconsistent",
             roe="6 %", roce="7 %", mos=-50, mf="NEGATIVE MONEY FLOW")


def _price(rs=72, ex3=10, ex12=8, a200=True, golden=True, a50=True, pos=75):
    return {"ticker": "X.NS", "technical": {"rs_rating": rs, "excess_3m": ex3, "excess_12m": ex12,
            "above_200dma": a200, "golden_cross": golden, "above_50dma": a50, "pos_52w": pos}}


WEAK_TECH = {"ticker": "X.NS", "technical": {"rs_rating": 8, "excess_3m": -14, "excess_12m": -20,
             "above_200dma": False, "golden_cross": False, "above_50dma": False, "pos_52w": 6}}


def test_results_strong_vs_weak():
    assert sg.score_results(STRONG)["score"] > 65
    assert sg.score_results(WEAK)["score"] < 40


def test_technical_strong_vs_weak():
    assert sg.score_technical(_price()["technical"])["score"] >= 70
    assert sg.score_technical(WEAK_TECH["technical"])["score"] <= 25


def test_fundamental_score_reads_quality():
    assert sg.score_fundamental(STRONG)["score"] > 60
    assert sg.score_fundamental(WEAK)["score"] < 45


def test_buy_requires_confluence():
    # strong fundamentals + results but weak momentum -> confluence gate blocks BUY
    v = sg.technofunda_signal(STRONG, WEAK_TECH)
    assert v["label"] in ("NEUTRAL", "SELL")
    # add momentum -> BUY
    v2 = sg.technofunda_signal(STRONG, _price())
    assert v2["label"] == "BUY" and v2["composite"] >= 60
    assert set(v2["blocks"]) == {"results", "technical", "fundamental"}
    assert "disclaimer" in v2 and v2["reasons_pos"]


def test_sell_on_deterioration():
    v = sg.technofunda_signal(WEAK, WEAK_TECH)
    assert v["label"] == "SELL" and v["reasons_neg"]


# ----------------------------------------------------- insider-bias guardrails
def _ov(pe=None, price=None, bv=None, roe="18 %", roce="17 %"):
    o = {"ROE": roe, "ROCE": roce}
    if pe is not None:    o["Stock P/E"] = str(pe)
    if price is not None: o["Current Price"] = "₹ %s" % price
    if bv is not None:    o["Book Value"] = "₹ %s" % bv
    return o


def test_bias_check_shape_and_attached():
    v = sg.technofunda_signal(STRONG, _price())
    bc = v["bias_check"]
    assert set(bc) >= {"risk", "principle", "flags", "source"}
    assert bc["risk"] in ("LOW", "MODERATE", "ELEVATED")
    for f in bc["flags"]:
        assert set(f) >= {"level", "title", "note", "lesson"}
        assert f["level"] in ("warn", "caution", "ok", "info")


def test_bias_valuation_blindspot_warns():
    fund = dict(STRONG)
    fund["overview"] = _ov(pe=95, price=1200, bv=60)            # P/E 95, P/B 20
    fund["analysis"] = dict(STRONG["analysis"]); fund["analysis"]["dcf"] = {"margin_of_safety": -55}
    v = sg.technofunda_signal(fund, _price())
    titles = [f["title"] for f in v["bias_check"]["flags"]]
    assert "Valuation blind-spot" in titles
    assert v["bias_check"]["risk"] == "ELEVATED"


def test_bias_sector_headwind_flag():
    v = sg.technofunda_signal(STRONG, _price(),
                              sector={"name": "Realty", "label": "HEADWIND", "score": -0.68})
    titles = [f["title"] for f in v["bias_check"]["flags"]]
    assert "Sector headwind" in titles
    assert v["bias_check"]["risk"] == "ELEVATED"


def test_bias_sector_tailwind_is_ok_not_risky():
    v = sg.technofunda_signal(STRONG, _price(),
                              sector={"name": "Utilities", "label": "TAILWIND", "score": 0.5})
    flags = {f["title"]: f["level"] for f in v["bias_check"]["flags"]}
    assert flags.get("Sector tailwind") == "ok"


def test_bias_turnaround_when_results_improve_but_tape_weak():
    v = sg.technofunda_signal(STRONG, WEAK_TECH)        # improving results, weak price -> NEUTRAL/SELL
    assert v["label"] in ("NEUTRAL", "SELL")
    titles = [f["title"] for f in v["bias_check"]["flags"]]
    assert "Turnaround vs. sentiment" in titles


def test_bias_clean_case_is_low_risk():
    mod = _fund([50, 50, 51, 52, 52, 53, 53, 54], [100, 101, 102, 103, 104, 105, 106, 108],
                [5, 5, 5.1, 5.1, 5.2, 5.2, 5.3, 5.4], [18] * 8, mos=8)
    mod["overview"] = _ov(pe=24, price=300, bv=120)
    v = sg.technofunda_signal(mod, _price(rs=52, ex3=1, pos=48, golden=False))
    bc = v["bias_check"]
    assert bc["risk"] == "LOW"
    assert all(f["level"] != "warn" for f in bc["flags"])


def test_sectorlookup_reads_json(tmp_path):
    from earnings_intel.data import sectorlookup as sl
    d = tmp_path / "data"; d.mkdir()
    (d / "sector_tailwind.json").write_text(json.dumps({"sectors": [
        {"sector": "Realty", "signal": "HEADWIND", "score": -0.68},
        {"sector": "Utilities", "signal": "TAILWIND", "score": 0.5}]}))
    (d / "sector_stocks.json").write_text(json.dumps({"sectors": {
        "Realty": [{"code": "DLF", "name": "DLF Ltd"}],
        "Utilities": [{"code": "NTPC", "name": "NTPC"}]}}))
    assert sl.sector_for("dlf", docs_dir=str(tmp_path)) == {"name": "Realty", "label": "HEADWIND", "score": -0.68}
    assert sl.sector_for(None, name="NTPC", docs_dir=str(tmp_path))["label"] == "TAILWIND"
    assert sl.sector_for("NOPE", docs_dir=str(tmp_path)) is None
