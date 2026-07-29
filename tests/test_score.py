"""Analysis Score (earnings_intel/data/score.py) — deterministic, no network."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.score import analysis_score, WEIGHTS, MAX_PENALTY  # noqa: E402


def _bundle(**over):
    b = {"fundamental": {
        "overview": {"Stock P/E": "20", "Book Value": "₹ 100"},
        "growth": {"Compounded Sales Growth": {"5 Years": "18%"},
                   "Compounded Profit Growth": {"5 Years": "22%"},
                   "Stock Price CAGR": {"1 Year": "25%"}},
        "analysis": {"dcf": {"margin_of_safety": 20},
                     "health": {"debt_equity": {"value": 0.2},
                                "current_ratio": {"value": 2.1},
                                "ocf_np": {"value": 1.2},
                                "peers": {"pe": {"value": 20, "sector": 30},
                                          "pb": {"value": 3, "sector": 5}}}}},
        "signal": {"blocks": {"technical": {"score": 70}, "results": {"score": 80}}}}
    b.update(over)
    return b


def test_score_is_zero_to_ten_with_named_components():
    s = analysis_score(_bundle(), sector={"sector": "Chemicals", "signal": "TAILWIND"})
    assert 0 <= s["score"] <= 10
    labels = {c["label"] for c in s["components"]}
    assert labels == {lbl for lbl, _ in WEIGHTS.values()}
    assert s["components_missing"] == []
    for c in s["components"]:
        assert 0 <= c["score"] <= 10 and c["note"]      # every component states its numbers


def test_a_good_company_outscores_a_bad_one():
    good = analysis_score(_bundle(), sector={"sector": "X", "signal": "TAILWIND"})
    bad = _bundle()
    bad["fundamental"]["analysis"]["health"] = {
        "debt_equity": {"value": 2.5}, "current_ratio": {"value": 0.6},
        "ocf_np": {"value": -0.4},
        "peers": {"pe": {"value": 90, "sector": 20}}}
    bad["fundamental"]["analysis"]["dcf"] = {"margin_of_safety": -70}
    bad["fundamental"]["growth"] = {"Compounded Sales Growth": {"5 Years": "-8%"}}
    bad["signal"]["blocks"] = {"technical": {"score": 20}, "results": {"score": 25}}
    worse = analysis_score(bad, sector={"sector": "X", "signal": "HEADWIND"})
    assert good["score"] > worse["score"]


def test_missing_component_is_dropped_and_weight_redistributed():
    """A thin bundle must score over what it HAS, not treat absence as zero."""
    thin = {"fundamental": {"analysis": {"health": {"debt_equity": {"value": 0.1},
                                                    "current_ratio": {"value": 3.0},
                                                    "ocf_np": {"value": 1.4}}}}}
    s = analysis_score(thin)
    assert [c["key"] for c in s["components"]] == ["financial_health"]
    assert "Growth" in s["components_missing"] and "Valuation" in s["components_missing"]
    assert s["score"] > 7          # strong health alone, not diluted by absent parts


def test_no_usable_input_is_honest_rather_than_a_number():
    s = analysis_score({})
    assert s["score"] is None
    assert s["label"] == "Not enough data"


def test_bias_check_dict_shape_yields_a_readable_flag():
    """signal.bias_check is a DICT; iterating it directly gave a flag reading just 'risk'."""
    b = _bundle(signal={"bias_check": {"risk": "ELEVATED", "principle": "...",
                                       "flags": [{"level": "warn", "title": "Valuation blind-spot",
                                                  "note": "P/E 46 with DCF overvalued"}]}})
    reasons = analysis_score(b)["red_flags"]["reasons"]
    assert reasons and "Valuation blind-spot" in reasons[0]
    assert "P/E 46" in reasons[0]
    assert not any(r.strip().lower() == "risk" for r in reasons)


def test_ok_level_flags_are_not_penalties():
    b = _bundle(signal={"bias_check": {"flags": [{"level": "ok", "title": "Sector tailwind",
                                                  "note": "Chemicals is in a TAILWIND"}]}})
    assert analysis_score(b)["red_flags"]["reasons"] == []


def test_penalty_is_capped_and_subtracted():
    flags = [{"level": "warn", "title": f"Flag {i}", "note": "something material is wrong here"}
             for i in range(12)]
    s = analysis_score(_bundle(signal={"bias_check": {"flags": flags},
                                       "blocks": {"technical": {"score": 70}}}))
    assert s["red_flags"]["penalty"] == MAX_PENALTY
    assert round(s["base"] - MAX_PENALTY, 1) == s["score"]


def test_negative_cash_flow_and_leverage_are_flagged():
    b = _bundle()
    b["fundamental"]["analysis"]["health"]["ocf_np"] = {"value": -0.8}
    b["fundamental"]["analysis"]["health"]["debt_equity"] = {"value": 2.2}
    joined = " ".join(analysis_score(b)["red_flags"]["reasons"]).lower()
    assert "negative" in joined and "leverage" in joined


def test_why_lists_come_from_swot_with_evidence():
    swot = {"strengths": [{"point": "Debt free", "evidence": "D/E 0.02x", "weight": 3}],
            "weaknesses": [{"point": "Expensive", "evidence": "P/E 46", "weight": 3}],
            "threats": []}
    s = analysis_score(_bundle(), swot=swot)
    assert s["why_invest"][0]["evidence"] == "D/E 0.02x"
    assert s["why_not"][0]["evidence"] == "P/E 46"


def test_indian_number_formats_parse():
    b = _bundle()
    b["fundamental"]["overview"]["Stock P/E"] = "₹ 1,23,456"
    assert analysis_score(b)["score"] is not None


def test_is_deterministic_and_does_not_mutate_input():
    import copy
    b = _bundle()
    snapshot = copy.deepcopy(b)
    a1 = analysis_score(b, sector={"sector": "X", "signal": "NEUTRAL"})
    a2 = analysis_score(b, sector={"sector": "X", "signal": "NEUTRAL"})
    assert a1 == a2
    assert b == snapshot


def test_garbage_never_raises():
    for junk in (None, [], "nope", {"fundamental": "bad"}, {"signal": 7}):
        out = analysis_score(junk)          # type: ignore[arg-type]
        assert "score" in out
