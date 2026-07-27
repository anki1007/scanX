"""Historical Comparison Agent — deterministic diffs and templated summaries.

Fixtures only: no network, no API key, no model. Everything here must be
reproducible byte-for-byte, so the assertions pin exact numbers and wording.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from earnings_intel.docpipe import compare as C   # noqa: E402


def _fact(claim, date="2026-05-01", kind="concall_transcript",
          url="https://example.com/q4fy26-transcript.pdf"):
    """A docanalysis FACT object as the extraction agent emits it."""
    return {"claim": claim, "quote": f"...{claim}...", "doc_kind": kind,
            "doc_date": date, "url": url}


PREV_GUIDANCE = [
    _fact("Management guided to an EBITDA margin of 12-14% for FY27", "2026-02-01",
          "concall_ppt", "https://example.com/q3fy26.pdf"),
    _fact("Capex of Rs 15,000 crore is planned for FY27", "2026-02-01",
          "concall_ppt", "https://example.com/q3fy26.pdf"),
    _fact("The company will be net debt free by FY27", "2026-02-01",
          "concall_ppt", "https://example.com/q3fy26.pdf"),
    _fact("Volume growth of 8% is expected in FY27", "2026-02-01",
          "concall_ppt", "https://example.com/q3fy26.pdf"),
]
CURR_GUIDANCE = [
    _fact("Management guided to an EBITDA margin of 14-16% for FY27"),
    _fact("Capex of Rs 18,000 crore is planned for FY27"),
    _fact("The company will be net debt free by FY27"),
    _fact("Management expects a dividend payout ratio of 30%"),
]

PREV_RISKS = [
    _fact("Commodity inflation added 200 bps of cost pressure", "2026-02-01"),
    _fact("The plant shutdown in Pune disrupted supply", "2026-02-01"),
]
CURR_RISKS = [
    _fact("Commodity inflation added 350 bps of cost pressure"),
    _fact("New import tariffs on components are a headwind"),
]


# ------------------------------------------------------------ diff_financials
def test_diff_financials_change_pct_and_direction():
    d = C.diff_financials({"revenue": 1200.0, "pat": 90.0},
                          {"revenue": 1000.0, "pat": 100.0})["metrics"]
    assert d["revenue"] == {"metric": "revenue", "old": 1000.0, "new": 1200.0,
                            "change": 200.0, "pct_change": 20.0,
                            "direction": "improved", "material": True,
                            "lower_is_better": False}
    assert d["pat"]["change"] == -10.0
    assert d["pat"]["pct_change"] == -10.0
    assert d["pat"]["direction"] == "declined"


def test_diff_financials_rounds_pct_to_two_places():
    d = C.diff_financials({"ebitda_margin": 14.0}, {"ebitda_margin": 12.0})
    assert d["metrics"]["ebitda_margin"]["pct_change"] == 16.67


def test_diff_financials_flat_when_unchanged():
    d = C.diff_financials({"pat": 100.0}, {"pat": 100.0})
    m = d["metrics"]["pat"]
    assert m["direction"] == "flat" and m["change"] == 0.0
    assert m["pct_change"] == 0.0 and m["material"] is False
    assert d["flat"] == ["pat"] and d["material"] == []


def test_diff_financials_flat_band_is_configurable():
    d = C.diff_financials({"pat": 102.0}, {"pat": 100.0}, flat_pct=5.0)
    assert d["metrics"]["pat"]["direction"] == "flat"
    assert d["metrics"]["pat"]["pct_change"] == 2.0     # still reported, just flat


def test_diff_financials_materiality_is_inclusive_at_the_threshold():
    at = C.diff_financials({"revenue": 110.0}, {"revenue": 100.0})["metrics"]["revenue"]
    under = C.diff_financials({"revenue": 109.0}, {"revenue": 100.0})["metrics"]["revenue"]
    assert at["pct_change"] == 10.0 and at["material"] is True
    assert under["pct_change"] == 9.0 and under["material"] is False


def test_diff_financials_material_threshold_is_configurable():
    d = C.diff_financials({"revenue": 109.0}, {"revenue": 100.0}, material_pct=5.0)
    assert d["metrics"]["revenue"]["material"] is True
    assert d["material"] == ["revenue"]
    assert d["params"] == {"material_pct": 5.0, "flat_pct": 0.0}


def test_diff_financials_zero_base_has_no_pct_but_is_material():
    up = C.diff_financials({"other_income": 15.0}, {"other_income": 0.0})
    m = up["metrics"]["other_income"]
    assert m["pct_change"] is None and m["change"] == 15.0
    assert m["direction"] == "improved" and m["material"] is True
    assert up["material"] == ["other_income"]

    down = C.diff_financials({"other_income": -3.0}, {"other_income": 0.0})
    assert down["metrics"]["other_income"]["direction"] == "declined"
    assert down["metrics"]["other_income"]["material"] is True


def test_diff_financials_zero_to_zero_is_flat_not_material():
    m = C.diff_financials({"x": 0.0}, {"x": 0.0})["metrics"]["x"]
    assert m["pct_change"] == 0.0 and m["direction"] == "flat"
    assert m["material"] is False


def test_diff_financials_negative_base_uses_absolute_denominator():
    # a loss narrowing from -10 to -5 is a 50% improvement, not -50%
    m = C.diff_financials({"exceptional": -5.0}, {"exceptional": -10.0})["metrics"]["exceptional"]
    assert m["pct_change"] == 50.0
    assert m["direction"] == "improved"

    worse = C.diff_financials({"exceptional": -20.0}, {"exceptional": -10.0})["metrics"]["exceptional"]
    assert worse["pct_change"] == -100.0 and worse["direction"] == "declined"


def test_diff_financials_lower_is_better_metrics_invert_direction():
    d = C.diff_financials({"net_debt": 4000.0, "employee_cost": 120.0},
                          {"net_debt": 5000.0, "employee_cost": 100.0})["metrics"]
    assert d["net_debt"]["lower_is_better"] is True
    assert d["net_debt"]["pct_change"] == -20.0
    assert d["net_debt"]["direction"] == "improved"
    assert d["employee_cost"]["direction"] == "declined"


def test_diff_financials_lower_is_better_can_be_pinned_explicitly():
    d = C.diff_financials({"churn": 4.0}, {"churn": 5.0}, lower_is_better=["revenue"])
    assert d["metrics"]["churn"]["lower_is_better"] is False
    assert d["metrics"]["churn"]["direction"] == "declined"   # override wins


def test_diff_financials_parses_strings_and_wrapped_values():
    d = C.diff_financials({"revenue": "1,200.0 cr", "margin": {"value": 14.0}},
                          {"revenue": "1,000 cr", "margin": {"value": 12.0}})
    assert d["metrics"]["revenue"]["new"] == 1200.0
    assert d["metrics"]["margin"]["old"] == 12.0


def test_diff_financials_one_sided_metrics_are_added_or_removed():
    d = C.diff_financials({"revenue": 1200.0, "new_kpi": 7.0, "note": "strong"},
                          {"revenue": 1000.0, "old_kpi": 3.0, "note": "ok"})
    assert set(d["metrics"]) == {"revenue"}
    assert d["added"] == {"new_kpi": 7.0}
    assert d["removed"] == {"old_kpi": 3.0}


def test_diff_financials_material_list_is_ordered_by_size_of_move():
    d = C.diff_financials({"a": 200.0, "b": 150.0, "c": 101.0},
                          {"a": 100.0, "b": 100.0, "c": 100.0})
    assert d["material"] == ["a", "b"]          # 100% then 50%; c is immaterial


def test_diff_financials_survives_empty_and_junk_input():
    empty = C.diff_financials({}, {})
    assert empty["metrics"] == {} and empty["material"] == []
    assert C.diff_financials(None, None)["metrics"] == {}
    assert C.diff_financials("nonsense", 42)["metrics"] == {}


# ------------------------------------------------------ diff_guidance / risks
def test_diff_guidance_splits_added_removed_changed_unchanged():
    g = C.diff_guidance(CURR_GUIDANCE, PREV_GUIDANCE)
    assert g["counts"] == {"added": 1, "removed": 1, "changed": 2, "unchanged": 1}
    assert [f["claim"] for f in g["added"]] == [
        "Management expects a dividend payout ratio of 30%"]
    assert [f["claim"] for f in g["removed"]] == [
        "Volume growth of 8% is expected in FY27"]
    assert g["unchanged"][0]["after"]["claim"] == "The company will be net debt free by FY27"
    assert g["unchanged"][0]["similarity"] == 1.0


def test_diff_guidance_changed_pairs_carry_before_after_and_similarity():
    g = C.diff_guidance(CURR_GUIDANCE, PREV_GUIDANCE)
    pairs = {(e["before"]["claim"], e["after"]["claim"]): e["similarity"]
             for e in g["changed"]}
    key = ("Management guided to an EBITDA margin of 12-14% for FY27",
           "Management guided to an EBITDA margin of 14-16% for FY27")
    assert key in pairs
    assert 0.6 <= pairs[key] < 1.0
    # sorted most-similar first, so the smallest edits surface together
    assert [e["similarity"] for e in g["changed"]] == sorted(
        [e["similarity"] for e in g["changed"]], reverse=True)


def test_diff_guidance_same_wording_different_number_is_changed_not_unchanged():
    curr = [_fact("Capex of Rs 18,000 crore is planned for FY27")]
    prev = [_fact("Capex of Rs 15,000 crore is planned for FY27", "2026-02-01")]
    g = C.diff_guidance(curr, prev)
    assert g["counts"]["changed"] == 1 and g["counts"]["unchanged"] == 0
    assert g["changed"][0]["similarity"] > C.DEFAULT_SAME_RATIO   # reads identical...
    # ...but the figure moved, so it must never be filed as a reiteration


def test_diff_guidance_keeps_provenance_on_both_sides():
    g = C.diff_guidance(CURR_GUIDANCE, PREV_GUIDANCE)
    entry = g["changed"][0]
    for side in ("before", "after"):
        for field in ("claim", "quote", "doc_kind", "doc_date", "url"):
            assert field in entry[side]
    assert entry["before"]["url"] == "https://example.com/q3fy26.pdf"
    assert entry["after"]["url"] == "https://example.com/q4fy26-transcript.pdf"


def test_diff_guidance_pairing_does_not_depend_on_input_order():
    a = C.diff_guidance(CURR_GUIDANCE, PREV_GUIDANCE)
    b = C.diff_guidance(list(reversed(CURR_GUIDANCE)), list(reversed(PREV_GUIDANCE)))
    as_pairs = lambda d: {(e["before"]["claim"], e["after"]["claim"])  # noqa: E731
                          for e in d["changed"] + d["unchanged"]}
    assert as_pairs(a) == as_pairs(b)
    assert {f["claim"] for f in a["added"]} == {f["claim"] for f in b["added"]}
    assert {f["claim"] for f in a["removed"]} == {f["claim"] for f in b["removed"]}


def test_diff_guidance_unrelated_claims_never_pair_up():
    g = C.diff_guidance([_fact("Management expects a dividend payout ratio of 30%")],
                        [_fact("Volume growth of 8% is expected in FY27", "2026-02-01")])
    assert g["counts"] == {"added": 1, "removed": 1, "changed": 0, "unchanged": 0}


def test_diff_guidance_handles_empty_sides_and_bare_strings():
    first = C.diff_guidance(CURR_GUIDANCE, [])
    assert first["counts"]["added"] == 4 and first["counts"]["removed"] == 0
    gone = C.diff_guidance([], PREV_GUIDANCE)
    assert gone["counts"]["removed"] == 4 and gone["counts"]["added"] == 0
    assert C.diff_guidance(None, None)["counts"]["added"] == 0
    # a bare string is accepted as a claim-only fact
    assert C.diff_guidance(["Margin guidance raised"], [])["added"][0]["claim"] == \
        "Margin guidance raised"


def test_diff_risks_has_the_same_shape_and_its_own_theme():
    r = C.diff_risks(CURR_RISKS, PREV_RISKS)
    assert r["theme"] == "risks_headwinds"
    assert C.diff_guidance([], [])["theme"] == "guidance"
    assert set(r) == set(C.diff_guidance([], []))
    assert r["counts"] == {"added": 1, "removed": 1, "changed": 1, "unchanged": 0}
    assert [f["claim"] for f in r["added"]] == [
        "New import tariffs on components are a headwind"]
    assert [f["claim"] for f in r["removed"]] == [
        "The plant shutdown in Pune disrupted supply"]


def test_diff_thresholds_are_configurable():
    curr = [_fact("Margin guidance of 15% for FY27")]
    prev = [_fact("Margin guidance of 12% for FY27", "2026-02-01")]
    assert C.diff_guidance(curr, prev, change_ratio=0.99)["counts"]["changed"] == 0
    assert C.diff_guidance(curr, prev, change_ratio=0.99)["counts"]["added"] == 1


# --------------------------------------------------------- summarise_changes
def test_summarise_changes_wording_is_deterministic():
    bullets = C.summarise_changes({"guidance": C.diff_guidance(CURR_GUIDANCE,
                                                               PREV_GUIDANCE)})
    assert bullets == [
        "Capex guidance moved from Rs 15,000 crore to Rs 18,000 crore.",
        "EBITDA margin guidance moved from 12-14% to 14-16%.",
        "New guidance: Management expects a dividend payout ratio of 30%.",
        "Guidance dropped: Volume growth of 8% is expected in FY27.",
    ]
    assert bullets == C.summarise_changes(
        {"guidance": C.diff_guidance(CURR_GUIDANCE, PREV_GUIDANCE)})


def test_summarise_changes_risk_wording():
    assert C.summarise_changes({"risks": C.diff_risks(CURR_RISKS, PREV_RISKS)}) == [
        "Commodity inflation risk moved from 200 bps to 350 bps.",
        "New risk: New import tariffs on components are a headwind.",
        "Risk dropped: The plant shutdown in Pune disrupted supply.",
    ]


def test_summarise_changes_financial_wording_and_ordering():
    fin = C.diff_financials({"revenue": 1200.0, "net_debt": 4000.0,
                             "other_income": 15.0, "pat": 100.0},
                            {"revenue": 1000.0, "net_debt": 5000.0,
                             "other_income": 0.0, "pat": 100.0})
    assert C.summarise_changes(fin) == [
        "Other income improved from 0 to 15 (no prior base).",
        "Net debt improved 20.0% (5,000 -> 4,000).",
        "Revenue improved 20.0% (1,000 -> 1,200).",
    ]   # flat metrics produce no bullet at all


def test_summarise_changes_falls_back_when_the_move_is_not_one_number():
    g = C.diff_guidance([_fact("Margin of 15% is expected on revenue of 130 crore")],
                        [_fact("Margin of 12% is expected on revenue of 100 crore",
                               "2026-02-01")])
    assert C.summarise_changes(g) == [
        'Guidance changed: "Margin of 12% is expected on revenue of 100 crore" -> '
        '"Margin of 15% is expected on revenue of 130 crore".'
    ]


def test_summarise_changes_accepts_a_composite_and_orders_financials_first():
    fin = C.diff_financials({"revenue": 1200.0}, {"revenue": 1000.0})
    out = C.summarise_changes({"financials": fin,
                               "guidance": C.diff_guidance(CURR_GUIDANCE, PREV_GUIDANCE),
                               "risks": C.diff_risks(CURR_RISKS, PREV_RISKS)})
    assert out[0] == "Revenue improved 20.0% (1,000 -> 1,200)."
    assert out[1].startswith("Capex guidance moved")
    assert out.index("Commodity inflation risk moved from 200 bps to 350 bps.") > 1
    assert len(out) == 8


def test_summarise_changes_respects_the_limit_and_degrades_quietly():
    out = C.summarise_changes({"guidance": C.diff_guidance(CURR_GUIDANCE,
                                                           PREV_GUIDANCE)}, limit=2)
    assert len(out) == 2
    assert C.summarise_changes({}) == []
    assert C.summarise_changes(None) == []
    assert C.summarise_changes({"guidance": "not a diff"}) == []
    assert C.summarise_changes({"financials": {"metrics": {"x": {}}}}) == []


def test_pipeline_is_pure_and_never_opens_a_socket(monkeypatch):
    import socket

    def _boom(*a, **kw):
        raise AssertionError("docpipe.compare must never touch the network")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    diffs = {"financials": C.diff_financials({"revenue": 1200.0}, {"revenue": 1000.0}),
             "guidance": C.diff_guidance(CURR_GUIDANCE, PREV_GUIDANCE),
             "risks": C.diff_risks(CURR_RISKS, PREV_RISKS)}
    assert C.summarise_changes(diffs)
    # and the inputs were not mutated on the way through
    assert CURR_GUIDANCE[0]["claim"] == \
        "Management guided to an EBITDA margin of 14-16% for FY27"


def test_diffs_are_json_serialisable():
    import json
    payload = {"financials": C.diff_financials({"revenue": 1200.0}, {"revenue": 1000.0}),
               "guidance": C.diff_guidance(CURR_GUIDANCE, PREV_GUIDANCE),
               "risks": C.diff_risks(CURR_RISKS, PREV_RISKS)}
    assert json.loads(json.dumps(payload)) == payload
