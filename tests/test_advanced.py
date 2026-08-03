"""The advanced fundamental checklist.

The cases that matter here are the ones where a naive implementation lies:
a growing company's depreciation, two statements covering different years, and
every check whose data simply is not in these statements.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.advanced import (  # noqa: E402
    CHECKS, FAIL, NA, PASS, WARN, evaluate,
)


def _stmt(headers, **rows):
    return {"headers": list(headers), "rows": {k.replace("_", " "): list(v) for k, v in rows.items()}}


def _bundle(**sections):
    return {"fundamental": sections}


def _by_key(result, key):
    for c in result["checks"]:
        if c["key"] == key:
            return c
    raise AssertionError(f"no check {key!r}")


YEARS = ["Mar 2023", "Mar 2024", "Mar 2025", "Mar 2026"]


# --------------------------------------- the bug a naive version would have

def test_growing_depreciation_is_not_called_fraud():
    """A company that keeps buying plant depreciates more every year. On a real
    bundle the charge went 8,590 to 17,171 -- a 30% swing on the raw series,
    which the first version flagged as revised asset lives. Against the asset
    base it is flat."""
    r = evaluate(_bundle(
        profit_loss=_stmt(YEARS, Depreciation=[100, 140, 180, 220]),
        balance_sheet=_stmt(YEARS, Fixed_Assets=[1000, 1400, 1800, 2200]),
    ))
    assert _by_key(r, "depreciation_volatility")["verdict"] == PASS


def test_a_revised_asset_life_is_still_caught():
    """The charge falling while the asset base grows is the actual red flag."""
    r = evaluate(_bundle(
        profit_loss=_stmt(YEARS, Depreciation=[200, 190, 90, 85]),
        balance_sheet=_stmt(YEARS, Fixed_Assets=[1000, 1100, 1200, 1300]),
    ))
    assert _by_key(r, "depreciation_volatility")["verdict"] == FAIL


def test_statements_are_matched_by_year_not_by_index():
    """The balance sheet starts a year earlier than the P&L and the P&L ends
    with a TTM column. Pairing by index divides one year's charge by another
    year's assets."""
    r = evaluate(_bundle(
        profit_loss=_stmt(["Mar 2024", "Mar 2025", "Mar 2026", "TTM"],
                          Depreciation=[140, 180, 220, 230]),
        balance_sheet=_stmt(["Mar 2023", "Mar 2024", "Mar 2025", "Mar 2026"],
                            Fixed_Assets=[1000, 1400, 1800, 2200]),
    ))
    # 140/1400, 180/1800, 220/2200 -> exactly 10% every year.
    check = _by_key(r, "depreciation_volatility")
    assert check["verdict"] == PASS
    assert check["value"] == 0.0, "years were not aligned"


# ------------------------------------------------------ individual checks

def test_capex_double_the_net_block_is_a_must_read():
    r = evaluate(_bundle(balance_sheet=_stmt(YEARS,
                                             Fixed_Assets=[100, 100, 100, 100],
                                             CWIP=[10, 40, 120, 250])))
    c = _by_key(r, "capex_intensity")
    assert c["verdict"] == PASS and c["value"] == 2.5
    assert "doubled" in c["detail"]


def test_maintenance_capex_is_flagged_as_no_step_change():
    r = evaluate(_bundle(balance_sheet=_stmt(YEARS,
                                             Fixed_Assets=[100, 100, 100, 100],
                                             CWIP=[5, 5, 5, 5])))
    assert _by_key(r, "capex_intensity")["verdict"] == WARN


def test_deleveraging_passes_and_rising_debt_fails():
    down = evaluate(_bundle(balance_sheet=_stmt(YEARS, Borrowings=[100, 80, 60, 40])))
    up = evaluate(_bundle(balance_sheet=_stmt(YEARS, Borrowings=[40, 70, 100, 140])))
    assert _by_key(down, "deleveraging")["verdict"] == PASS
    assert _by_key(up, "deleveraging")["verdict"] == FAIL


def test_half_the_balance_sheet_in_receivables_and_stock_fails():
    """The checklist's fictitious-sales tell."""
    r = evaluate(_bundle(
        profit_loss=_stmt(YEARS, Sales=[365, 365, 365, 365]),
        balance_sheet=_stmt(YEARS, Total_Assets=[200, 200, 200, 200]),
        ratios=_stmt(YEARS, Debtor_Days=[120, 120, 120, 120],
                     Inventory_Days=[60, 60, 60, 60]),
    ))
    c = _by_key(r, "recv_inv_share")
    assert c["verdict"] == FAIL
    assert c["value"] == 90.0


def test_interest_cover_below_seven_is_not_a_pass():
    thin = evaluate(_bundle(profit_loss=_stmt(YEARS, Operating_Profit=[100] * 4,
                                              Interest=[25] * 4)))
    assert _by_key(thin, "interest_cover")["verdict"] == WARN
    strong = evaluate(_bundle(profit_loss=_stmt(YEARS, Operating_Profit=[100] * 4,
                                                Interest=[5] * 4)))
    assert _by_key(strong, "interest_cover")["verdict"] == PASS


def test_no_debt_is_not_a_division_by_zero():
    r = evaluate(_bundle(profit_loss=_stmt(YEARS, Operating_Profit=[100] * 4,
                                           Interest=[0, 0, 0, 0])))
    assert _by_key(r, "interest_cover")["verdict"] == PASS


def test_operating_leverage_wants_sales_outpacing_costs():
    good = evaluate(_bundle(profit_loss=_stmt(YEARS, Sales=[100, 120, 150, 190],
                                              Expenses=[80, 88, 96, 105])))
    bad = evaluate(_bundle(profit_loss=_stmt(YEARS, Sales=[100, 105, 108, 110],
                                             Expenses=[80, 95, 105, 118])))
    assert _by_key(good, "operating_leverage")["verdict"] == PASS
    assert _by_key(bad, "operating_leverage")["verdict"] == FAIL


def test_peak_margin_on_a_peak_multiple_is_the_zone_of_danger():
    r = evaluate(_bundle(
        profit_loss=_stmt(YEARS, OPM_=[10, 14, 18, 25]),
        overview={"Stock P/E": "62"},
    ))
    c = _by_key(r, "zone_of_danger")
    assert c["verdict"] == FAIL
    r2 = evaluate(_bundle(
        profit_loss=_stmt(YEARS, OPM_=[25, 20, 15, 12]),
        overview={"Stock P/E": "18"},
    ))
    assert _by_key(r2, "zone_of_danger")["verdict"] == PASS


def test_cyclicality_is_reported_not_scored():
    """Cyclical is a different game, not a worse business."""
    r = evaluate(_bundle(analysis={"cyclical": {"label": "CYCLICAL",
                                                "positive_quarters": ["Mar"]}}))
    c = _by_key(r, "cyclical")
    assert c["verdict"] == NA
    assert "CYCLICAL" in c["detail"] and "Mar" in c["detail"]


# ------------------------------------- honesty about what cannot be computed

def test_checks_without_data_say_so_rather_than_passing():
    r = evaluate({})
    for c in r["checks"]:
        assert c["verdict"] == NA, f"{c['key']} claimed a verdict with no data"
    assert r["score"] is None
    assert r["counts"]["decided"] == 0


def test_the_permanently_unavailable_checks_are_declared():
    """These need lines the statements do not carry. They must never quietly
    read as a pass."""
    r = evaluate(_bundle(profit_loss=_stmt(YEARS, Sales=[1, 2, 3, 4])))
    for key in ("cash_pile", "volume_vs_value", "inventory_writeoff", "corporate_actions"):
        c = _by_key(r, key)
        assert c["verdict"] == NA
        assert c["detail"], f"{key} gives no reason"


def test_gross_margin_is_never_claimed():
    """There is no raw-material line, so the operating margin is what is
    computed and it must be named that way."""
    r = evaluate(_bundle(profit_loss=_stmt(YEARS, OPM_=[10, 12, 14, 16])))
    c = _by_key(r, "margin_trend")
    assert "operating margin" in c["label"].lower()
    assert "gross margin is not available" in c["detail"]


def test_score_reflects_only_decidable_checks():
    r = evaluate(_bundle(profit_loss=_stmt(YEARS, Operating_Profit=[100] * 4,
                                           Interest=[1] * 4)))
    assert r["counts"]["decided"] >= 1
    assert r["score"] is not None
    assert 0 <= r["score"] <= 100


# ---------------------------------------------------------------- hygiene

def test_every_declared_check_is_always_emitted():
    """The page renders a fixed table; a missing row would shift the columns."""
    for bundle in ({}, _bundle(), _bundle(profit_loss=_stmt(YEARS, Sales=[1, 2, 3, 4]))):
        keys = {c["key"] for c in evaluate(bundle)["checks"]}
        assert set(CHECKS) <= keys, f"missing {set(CHECKS) - keys}"


def test_junk_never_raises():
    for junk in (None, {}, [], "x", {"fundamental": []}, {"fundamental": {"profit_loss": []}},
                 {"fundamental": {"balance_sheet": {"headers": None, "rows": None}}}):
        out = evaluate(junk)
        assert isinstance(out, dict) and "checks" in out


# ----------------------------------------- percentage changes that are lies

def test_a_tiny_base_cannot_produce_a_million_percent():
    """A metric starting at 0.001 produced a 5,722,469% 'change' on real data.
    The near-zero guard only catches an exact zero; it cannot catch a small
    one."""
    from earnings_intel.data.advanced import _trend
    assert _trend([0.001, 10, 40, 57.2]) == 5.0


def test_a_series_crossing_zero_is_clamped_not_reported_as_189_percent():
    """Working-capital days going from +40 to -35 is a good outcome, but
    'falling 189%' is not a sentence about anything."""
    from earnings_intel.data.advanced import _trend
    assert _trend([40, 20, 0, -35.6]) == -1.0


def test_clamping_does_not_change_any_verdict():
    """Every threshold in the module sits between -25% and +25%, so a clamp at
    the extremes must not move a company between pass and fail."""
    from earnings_intel.data.advanced import _trend
    assert _trend([40, 30, 20, 10]) == -0.75          # untouched, inside range
    assert _trend([100, 105, 108, 112]) == 0.12       # untouched


def test_ordinary_changes_pass_through_unclamped():
    from earnings_intel.data.advanced import _trend
    assert abs(_trend([100, 110, 120, 130]) - 0.30) < 1e-9


def test_reported_values_are_clamped_but_verdicts_are_not():
    """Real data produced a 6,588,781% cash conversion and a 112,166% margin
    variation, both from a near-zero denominator. The verdicts were defensible;
    the printed numbers were noise."""
    from earnings_intel.data.advanced import REPORT_CAP
    r = evaluate(_bundle(
        profit_loss=_stmt(YEARS, Operating_Profit=[100] * 4, Interest=[0.00001] * 4)))
    c = _by_key(r, "interest_cover")
    assert c["verdict"] == PASS, "the verdict must survive the clamp"
    assert abs(c["value"]) <= REPORT_CAP


def test_an_impossible_pe_is_not_treated_as_expensive():
    """A scraped P/E of 2,615,022 turned up on the real board."""
    r = evaluate(_bundle(
        profit_loss=_stmt(YEARS, OPM_=[10, 14, 18, 25]),
        overview={"Stock P/E": "2615022"},
    ))
    assert _by_key(r, "zone_of_danger")["verdict"] == NA


def test_a_real_high_pe_still_counts_as_expensive():
    r = evaluate(_bundle(
        profit_loss=_stmt(YEARS, OPM_=[10, 14, 18, 25]),
        overview={"Stock P/E": "85"},
    ))
    assert _by_key(r, "zone_of_danger")["verdict"] == FAIL
