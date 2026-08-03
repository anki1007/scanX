"""Deterministic SWOT engine — earnings_intel.data.swot.

Fixture bundles only: no network, no API key, no docs/data reads. Each rule
family is checked both firing and *not* firing, because the contract is that a
point only exists when its datum does.
"""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from earnings_intel.data import swot as W  # noqa: E402

HDRS = ["Mar 2024", "Mar 2025", "Mar 2026"]
QHDRS = ["Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026"]


# ------------------------------------------------------------------ builders
def _stmt(rows, headers=None):
    return {"headers": list(headers or HDRS), "rows": rows}


def _bundle(analysis=None, prices=None, signal=None, **fundamental):
    fund = {"code": "TEST", "name": "Test Ltd"}
    fund.update(fundamental)
    if analysis is not None:
        fund["analysis"] = analysis
    out = {"generated_at": "2026-07-29", "fundamental": fund}
    if prices is not None:
        out["prices"] = prices
    if signal is not None:
        out["signal"] = signal
    return out


def _health(**kw):
    return {"analysis": {"health": kw}}


def _metrics(res, quad):
    return [i["metric"] for i in res[quad]]


def _find(res, quad, metric):
    for item in res[quad]:
        if item["metric"] == metric:
            return item
    return None


def _fact(claim, quote, **kw):
    fact = {"claim": claim, "quote": quote, "doc_kind": "annual_report",
            "doc_date": "2026-03-31"}
    fact.update(kw)
    return fact


# ==================================================== Indian number parsing
def test_to_float_parses_indian_money_and_percent():
    assert W.to_float("₹ 26,877 Cr.") == 26877.0
    assert W.to_float("Rs 26,877 Cr.") == 26877.0
    assert W.to_float("1,23,456") == 123456.0
    assert W.to_float("23.5 %") == 23.5
    assert W.to_float("-3%") == -3.0
    assert W.to_float("₹ 665 / 363") == 665.0
    assert W.to_float("75.00%") == 75.0


def test_to_float_handles_missing_and_junk_cells():
    for junk in (None, "", "   ", "%", "-", "Rs", "n/a", True, False):
        assert W.to_float(junk) is None


def test_to_float_handles_signs_and_native_numbers():
    assert W.to_float("(1,234)") == -1234.0
    assert W.to_float("−2.5%") == -2.5          # unicode minus
    assert W.to_float(42) == 42.0
    assert W.to_float(-0.5) == -0.5
    assert W.to_float(float("nan")) is None
    assert W.to_float(float("inf")) is None


def test_fmt_is_compact_and_safe():
    assert W._fmt(26360.0) == "26,360"
    assert W._fmt(0.8234, "x") == "0.82x"
    assert W._fmt(23.50, "%") == "23.5%"
    assert W._fmt(None) == ""


# =============================================== empty / minimal input
def test_empty_bundle_returns_four_empty_lists_and_an_honest_verdict():
    res = W.build_swot({})
    assert res["strengths"] == []
    assert res["weaknesses"] == []
    assert res["opportunities"] == []
    assert res["threats"] == []
    assert res["score"] == {"s": 0, "w": 0, "o": 0, "t": 0}
    assert "No SWOT point could be evidenced" in res["verdict"]
    assert res["coverage"]["inputs_used"] == []
    assert "overview" in res["coverage"]["inputs_missing"]
    assert "filings" in res["coverage"]["inputs_missing"]


def test_garbage_input_does_not_raise():
    for junk in (None, [], "nope", {"fundamental": []}, {"fundamental": {"analysis": 7}}):
        res = W.build_swot(junk)
        assert set(res) == {"strengths", "weaknesses", "opportunities", "threats",
                            "score", "verdict", "coverage"}


def test_coverage_marks_what_was_actually_read():
    res = W.build_swot(_bundle(overview={"ROCE": "22 %"}, pros=["Good."]),
                       sector={"name": "Chemicals", "label": "TAILWIND", "score": 0.7})
    used = res["coverage"]["inputs_used"]
    assert "overview" in used and "pros" in used and "sector" in used
    assert "cons" in res["coverage"]["inputs_missing"]
    assert "filings" in res["coverage"]["inputs_missing"]


# ================================================================ returns
def test_high_roce_and_roe_fire_as_decisive_strengths():
    res = W.build_swot(_bundle(overview={"ROCE": "23.5 %", "ROE": "19.0 %"}))
    roce, roe = _find(res, "strengths", "roce"), _find(res, "strengths", "roe")
    assert roce["weight"] == 3 and roce["evidence"] == "ROCE 23.5 %"
    assert roe["weight"] == 3 and roe["evidence"] == "ROE 19.0 %"
    assert _find(res, "weaknesses", "roce") is None


def test_poor_roce_and_roe_fire_as_weaknesses():
    res = W.build_swot(_bundle(overview={"ROCE": "3.4 %", "ROE": "3.7 %"}))
    assert _find(res, "weaknesses", "roce")["weight"] == 3
    assert _find(res, "weaknesses", "roe")["weight"] == 3
    assert _find(res, "strengths", "roce") is None


def test_returns_rule_stays_silent_without_the_datum():
    res = W.build_swot(_bundle(overview={"ROCE": "", "ROE": "%"}))
    assert "roce" not in _metrics(res, "strengths") + _metrics(res, "weaknesses")
    assert "roe" not in _metrics(res, "strengths") + _metrics(res, "weaknesses")


def test_decade_long_roe_record_is_its_own_strength():
    growth = {"Return on Equity": {"10 Years": "20%", "5 Years": "20%",
                                   "3 Years": "17%", "Last Year": "18%"}}
    res = W.build_swot(_bundle(growth=growth))
    hit = _find(res, "strengths", "roe_history")
    assert hit["weight"] == 3 and "10 Years 20%" in hit["evidence"]


def test_sliding_roe_record_is_a_weakness():
    growth = {"Return on Equity": {"10 Years": "22%", "5 Years": "16%",
                                   "3 Years": "9%"}}
    res = W.build_swot(_bundle(growth=growth))
    assert _find(res, "weaknesses", "roe_history")["weight"] == 2
    assert _find(res, "strengths", "roe_history") is None


# ================================================================= growth
def test_strong_five_year_compounding_is_a_strength():
    growth = {"Compounded Profit Growth": {"10 Years": "24%", "5 Years": "26%",
                                           "3 Years": "25%", "TTM": "20%"}}
    res = W.build_swot(_bundle(growth=growth))
    hit = _find(res, "strengths", "profit_cagr_5y")
    assert hit["weight"] == 3
    assert hit["evidence"] == "Compounded Profit Growth 5 Years 26%"


def test_decelerating_growth_ladder_replaces_the_single_window_reads():
    growth = {"Compounded Sales Growth": {"10 Years": "16%", "5 Years": "4%",
                                          "3 Years": "-3%", "TTM": "-3%"}}
    res = W.build_swot(_bundle(growth=growth))
    ladder = _find(res, "weaknesses", "sales_growth_decel")
    assert ladder["weight"] == 2
    assert "10Y 16% -> 5Y 4% -> 3Y -3%" in ladder["evidence"]
    # the ladder subsumes them, so the 3Y / TTM reads are not repeated
    assert _find(res, "weaknesses", "sales_cagr_3y") is None
    assert _find(res, "weaknesses", "sales_growth_ttm") is None
    assert _find(res, "weaknesses", "sales_cagr_5y")["weight"] == 2


def test_three_year_contraction_fires_when_there_is_no_ladder():
    growth = {"Compounded Sales Growth": {"10 Years": "%", "5 Years": "%",
                                          "3 Years": "-8%", "TTM": "-2%"}}
    res = W.build_swot(_bundle(growth=growth))
    assert _find(res, "weaknesses", "sales_cagr_3y")["evidence"].endswith("-8%")
    assert _find(res, "weaknesses", "sales_growth_ttm") is None


def test_accelerating_growth_is_a_strength():
    growth = {"Compounded Profit Growth": {"10 Years": "8%", "5 Years": "9%",
                                           "3 Years": "30%", "TTM": "31%"}}
    res = W.build_swot(_bundle(growth=growth))
    assert _find(res, "strengths", "profit_growth_accel")["weight"] == 2


def test_growth_rule_is_silent_on_blank_screener_cells():
    growth = {"Compounded Sales Growth": {"10 Years": "%", "5 Years": "%",
                                          "3 Years": "%", "TTM": "%"}}
    res = W.build_swot(_bundle(growth=growth))
    assert res["weaknesses"] == [] and res["strengths"] == []


# ================================================================ margins
def test_margin_expansion_and_compression():
    up = _bundle(profit_loss=_stmt({"OPM %": ["15%", "17%", "21%"]},
                                   ["Mar 2023", "Mar 2025", "Mar 2026"]))
    up["fundamental"]["profit_loss"]["headers"] = ["Mar 2023", "Mar 2024",
                                                   "Mar 2025", "Mar 2026"]
    up["fundamental"]["profit_loss"]["rows"]["OPM %"] = ["15%", "16%", "17%", "21%"]
    res = W.build_swot(up)
    assert _find(res, "strengths", "opm_trend")["weight"] == 2

    down = _bundle(profit_loss={"headers": ["Mar 2023", "Mar 2024", "Mar 2025",
                                            "Mar 2026"],
                                "rows": {"OPM %": ["21%", "19%", "17%", "12%"]}})
    res = W.build_swot(down)
    assert _find(res, "weaknesses", "opm_trend")["weight"] == 2


def test_margin_rule_needs_four_periods():
    res = W.build_swot(_bundle(profit_loss=_stmt({"OPM %": ["10%", "14%", "22%"]})))
    assert _find(res, "strengths", "opm_trend") is None


def test_loss_making_company_is_flagged():
    res = W.build_swot(_bundle(profit_loss=_stmt({"Net Profit": ["12", "4", "-45"]})))
    hit = _find(res, "weaknesses", "net_profit")
    assert hit["weight"] == 3 and "-45" in hit["evidence"]


def test_interest_cover_strength_and_threat():
    fat = _bundle(profit_loss=_stmt({"Operating Profit": ["500", "600", "680"],
                                     "Interest": ["6", "7", "7"]}))
    res = W.build_swot(fat)
    assert _find(res, "strengths", "interest_coverage")["weight"] == 2

    thin = _bundle(profit_loss=_stmt({"Operating Profit": ["50", "40", "30"],
                                      "Interest": ["20", "24", "28"]}))
    res = W.build_swot(thin)
    assert _find(res, "weaknesses", "interest_coverage")["weight"] == 3
    assert _find(res, "threats", "interest_coverage")["weight"] == 3


def test_interest_cover_silent_when_there_is_no_interest_line():
    res = W.build_swot(_bundle(profit_loss=_stmt({"Operating Profit": ["50"]})))
    assert _find(res, "strengths", "interest_coverage") is None
    assert _find(res, "weaknesses", "interest_coverage") is None


# =========================================================== balance sheet
def test_debt_free_balance_sheet_is_decisive():
    res = W.build_swot(_bundle(**_health(debt_equity={"value": 0.02,
                                                      "year": "Mar 2026"})))
    hit = _find(res, "strengths", "debt_equity")
    assert hit["weight"] == 3 and hit["evidence"] == "Debt/Equity 0.02x (Mar 2026)"


def test_high_gearing_is_a_weakness_and_a_threat():
    res = W.build_swot(_bundle(**_health(debt_equity={"value": 2.4,
                                                      "year": "Mar 2026"})))
    assert _find(res, "weaknesses", "debt_equity")["weight"] == 3
    assert _find(res, "threats", "leverage_risk")["weight"] == 3


def test_debt_equity_falls_back_to_the_balance_sheet():
    res = W.build_swot(_bundle(balance_sheet=_stmt({
        "Borrowings": ["20", "22", "25"],
        "Equity Capital": ["50", "50", "50"],
        "Reserves": ["900", "1,000", "1,100"]})))
    hit = _find(res, "strengths", "debt_equity")
    assert hit["weight"] == 3 and "Borrowings 25 Cr" in hit["evidence"]


def test_leverage_rule_silent_without_any_debt_datum():
    res = W.build_swot(_bundle(balance_sheet=_stmt({"Reserves": ["1", "2", "3"]})))
    assert _find(res, "strengths", "debt_equity") is None
    assert _find(res, "weaknesses", "debt_equity") is None


def test_current_ratio_comfortable_and_tight():
    res = W.build_swot(_bundle(**_health(current_ratio={"value": 3.44,
                                                        "year": "history"})))
    assert _find(res, "strengths", "current_ratio")["weight"] == 2
    res = W.build_swot(_bundle(**_health(current_ratio={"value": 0.82})))
    hit = _find(res, "weaknesses", "current_ratio")
    assert hit["weight"] == 3 and hit["evidence"] == "Current ratio 0.82x"


def test_debt_paydown_is_an_opportunity_and_buildup_a_threat():
    res = W.build_swot(_bundle(balance_sheet=_stmt(
        {"Borrowings": ["63", "40", "10"]})))
    assert _find(res, "opportunities", "deleveraging")["weight"] == 2

    res = W.build_swot(_bundle(balance_sheet=_stmt(
        {"Borrowings": ["25", "40", "80"]})))
    assert _find(res, "weaknesses", "debt_buildup")["weight"] == 2
    assert _find(res, "threats", "debt_buildup")["weight"] == 2


def test_cwip_drawdown_reads_as_capex_completed_and_capacity_coming():
    res = W.build_swot(_bundle(**_health(cwip={"latest": 20.0, "prev": 100.0,
                                               "pct_change": -80.0,
                                               "year": "Mar 2026"})))
    assert _find(res, "strengths", "cwip_drawdown")["weight"] == 2
    hit = _find(res, "opportunities", "cwip_commissioning")
    assert hit["weight"] == 2 and "100 Cr -> 20 Cr" in hit["evidence"]


def test_cwip_buildup_is_only_an_opportunity():
    res = W.build_swot(_bundle(**_health(cwip={"latest": 90.0, "prev": 10.0,
                                               "pct_change": 800.0})))
    assert _find(res, "opportunities", "cwip_buildup")["weight"] == 2
    assert _find(res, "strengths", "cwip_drawdown") is None


def test_cwip_steady_emits_nothing():
    res = W.build_swot(_bundle(**_health(cwip={"latest": 20.0, "prev": 21.0,
                                               "pct_change": -4.8})))
    assert _find(res, "opportunities", "cwip_commissioning") is None
    assert _find(res, "opportunities", "cwip_buildup") is None


# =================================================================== cash
def test_cash_backed_profit_is_decisive():
    res = W.build_swot(_bundle(**_health(ocf_np={"value": 1.5, "year": "Mar 2026"})))
    hit = _find(res, "strengths", "ocf_np")
    assert hit["weight"] == 3
    assert hit["evidence"] == "Operating cash flow / net profit 1.5x (Mar 2026)"


def test_negative_ocf_np_is_a_decisive_weakness():
    res = W.build_swot(_bundle(**_health(ocf_np={"value": -0.4, "year": "Mar 2026"})))
    assert _find(res, "weaknesses", "ocf_np")["weight"] == 3
    assert _find(res, "strengths", "ocf_np") is None


def test_ocf_np_falls_back_to_the_statements():
    res = W.build_swot(_bundle(
        cash_flow=_stmt({"Cash from Operating Activity": ["80", "90", "120"]}),
        profit_loss=_stmt({"Net Profit": ["70", "80", "100"]})))
    assert _find(res, "strengths", "ocf_np")["weight"] == 3


def test_cash_burn_is_a_threat():
    res = W.build_swot(_bundle(cash_flow=_stmt(
        {"Cash from Operating Activity": ["30", "10", "-25"]})))
    hit = _find(res, "threats", "operating_cash_flow")
    assert hit["weight"] == 3 and "-25 Cr (Mar 2026)" in hit["evidence"]


def test_cfo_over_op_percent_row_is_read_as_a_percentage():
    res = W.build_swot(_bundle(cash_flow=_stmt({"CFO/OP": ["187%", "100%", "91%"]})))
    assert _find(res, "strengths", "cfo_op")["evidence"] == "CFO/Operating profit 91%"
    res = W.build_swot(_bundle(cash_flow=_stmt({"CFO/OP": ["60%", "50%", "30%"]})))
    assert _find(res, "weaknesses", "cfo_op")["weight"] == 2


def test_earnings_quality_gap_fires_when_pat_rises_and_cash_falls():
    res = W.build_swot(_bundle(
        profit_loss=_stmt({"Net Profit": ["80", "100", "140"]}),
        cash_flow=_stmt({"Cash from Operating Activity": ["120", "110", "70"]})))
    hit = _find(res, "threats", "earnings_quality")
    assert hit["weight"] == 3 and "140" in hit["evidence"] and "70" in hit["evidence"]


def test_earnings_quality_gap_silent_when_cash_follows_profit():
    res = W.build_swot(_bundle(
        profit_loss=_stmt({"Net Profit": ["80", "100", "140"]}),
        cash_flow=_stmt({"Cash from Operating Activity": ["70", "110", "160"]})))
    assert _find(res, "threats", "earnings_quality") is None


def test_operating_leverage_both_ways():
    res = W.build_swot(_bundle(profit_loss=_stmt(
        {"Sales": ["100", "110", "130"], "Expenses": ["80", "85", "88"]})))
    hit = _find(res, "opportunities", "operating_leverage")
    assert hit["weight"] == 2 and "Sales 18.2%" in hit["evidence"]

    res = W.build_swot(_bundle(profit_loss=_stmt(
        {"Sales": ["100", "110", "112"], "Expenses": ["80", "85", "104"]})))
    assert _find(res, "weaknesses", "operating_leverage")["weight"] == 2


# ======================================================== working capital
def test_stretched_receivables_are_a_weakness_and_a_threat():
    res = W.build_swot(_bundle(ratios=_stmt({"Debtor Days": ["60", "90", "130"]})))
    assert _find(res, "weaknesses", "debtor_days")["weight"] == 2
    hit = _find(res, "threats", "debtor_days_trend")
    assert hit["weight"] == 2 and "90 (Mar 2025) -> 130 (Mar 2026)" in hit["evidence"]


def test_healthy_receivables_emit_nothing():
    res = W.build_swot(_bundle(ratios=_stmt({"Debtor Days": ["40", "38", "35"]})))
    assert _find(res, "weaknesses", "debtor_days") is None
    assert _find(res, "threats", "debtor_days_trend") is None


def test_negative_working_capital_is_a_strength():
    res = W.build_swot(_bundle(ratios=_stmt({"Working Capital Days": ["5", "-2", "-18"],
                                             "Cash Conversion Cycle": ["3", "-4", "-20"]})))
    assert _find(res, "strengths", "working_capital_days")["weight"] == 2
    assert _find(res, "strengths", "cash_conversion_cycle")["weight"] == 2


def test_roce_ratio_row_trend_both_ways():
    hdrs = ["Mar 2023", "Mar 2024", "Mar 2025", "Mar 2026"]
    res = W.build_swot(_bundle(ratios={"headers": hdrs,
                                       "rows": {"ROCE %": ["12%", "15%", "18%", "21%"]}}))
    assert _find(res, "strengths", "roce_trend")["weight"] == 2
    res = W.build_swot(_bundle(ratios={"headers": hdrs,
                                       "rows": {"ROCE %": ["30%", "27%", "25%", "23%"]}}))
    hit = _find(res, "weaknesses", "roce_trend")
    assert hit["weight"] == 2 and "30% (Mar 2023) -> 23% (Mar 2026)" in hit["evidence"]


# ================================================================ trends
def test_yearly_uptrend_and_downtrend_from_analysis_trends():
    res = W.build_swot(_bundle(analysis={"trends": {
        "yearly": {"Sales": {"label": "Increasing", "n": 4, "unit": "yrs"},
                   "Net Profit": {"label": "Decreasing", "n": 5, "unit": "yrs"},
                   "EPS": {"label": "Inconsistent", "n": 2, "unit": "yrs"}}}}))
    up = _find(res, "strengths", "trend_yearly_sales")
    assert up["weight"] == 2 and "rising for 4 years" in up["point"]
    down = _find(res, "weaknesses", "trend_yearly_net_profit")
    assert down["weight"] == 2 and "Increasing" not in down["evidence"]
    assert _find(res, "strengths", "trend_yearly_eps") is None


def test_quarterly_trends_are_minor_and_holding_trends_route_to_flow():
    res = W.build_swot(_bundle(analysis={"trends": {
        "quarterly": {"Sales": {"label": "Increasing", "n": 5, "unit": "qtrs"},
                      "Promoter Holding": {"label": "Increasing", "n": 4,
                                           "unit": "qtrs"},
                      "Institutional Holding": {"label": "Decreasing", "n": 4,
                                                "unit": "qtrs"}}}}))
    assert _find(res, "strengths", "trend_quarterly_sales")["weight"] == 1
    assert _find(res, "strengths", "promoter_holding_trend") is not None
    assert _find(res, "opportunities", "promoter_holding_trend_flow") is not None
    assert _find(res, "threats", "institutional_holding_trend_flow")["weight"] == 2


def test_unknown_trend_metrics_are_ignored():
    res = W.build_swot(_bundle(analysis={"trends": {
        "yearly": {"Mystery Metric": {"label": "Increasing", "n": 9, "unit": "yrs"}}}}))
    assert res["strengths"] == []


# ============================================================== valuation
def test_positive_dcf_margin_of_safety_is_strength_and_opportunity():
    res = W.build_swot(_bundle(analysis={"dcf": {
        "ok": True, "intrinsic_per_share": 4683, "current_price": 2210,
        "margin_of_safety": 52.8}}))
    assert _find(res, "strengths", "dcf_mos")["weight"] == 3
    hit = _find(res, "opportunities", "dcf_valuation")
    assert hit["weight"] == 3 and "margin of safety 52.8%" in hit["evidence"]


def test_negative_dcf_margin_of_safety_is_weakness_and_derating_threat():
    res = W.build_swot(_bundle(analysis={"dcf": {
        "ok": True, "intrinsic_per_share": 264, "current_price": 528,
        "margin_of_safety": -99.8}}))
    assert _find(res, "weaknesses", "dcf_mos")["weight"] == 3
    assert _find(res, "threats", "valuation_derating")["weight"] == 2
    assert _find(res, "strengths", "dcf_mos") is None


def test_dcf_that_did_not_run_emits_nothing():
    res = W.build_swot(_bundle(analysis={"dcf": {"ok": False,
                                                 "margin_of_safety": 90}}))
    assert res["strengths"] == [] and res["opportunities"] == []


def test_reverse_dcf_expectations_versus_delivered_growth():
    low = _bundle(growth={"Compounded Profit Growth": {"5 Years": "51%"}},
                  analysis={"dcf": {"ok": True, "reverse": {"implied_growth": 7.69}}})
    res = W.build_swot(low)
    hit = _find(res, "opportunities", "reverse_dcf")
    assert hit["weight"] == 2 and "7.69%" in hit["evidence"] and "51%" in hit["evidence"]

    high = _bundle(growth={"Compounded Profit Growth": {"5 Years": "10%"}},
                   analysis={"dcf": {"ok": True, "reverse": {"implied_growth": 19.51}}})
    res = W.build_swot(high)
    assert _find(res, "threats", "reverse_dcf")["weight"] == 2
    assert _find(res, "opportunities", "reverse_dcf") is None


def test_multiples_rich_and_cheap():
    rich = W.build_swot(_bundle(overview={"Stock P/E": "61.8"}))
    assert _find(rich, "weaknesses", "pe_absolute")["weight"] == 2
    assert _find(rich, "threats", "valuation_derating")["weight"] == 2

    cheap = W.build_swot(_bundle(overview={"Stock P/E": "5.96"}))
    assert _find(cheap, "opportunities", "pe_absolute")["weight"] == 1
    assert _find(cheap, "weaknesses", "pe_absolute") is None


def test_price_to_book_below_one_is_an_opportunity():
    res = W.build_swot(_bundle(overview={"Current Price": "₹ 152",
                                         "Book Value": "₹ 173"}))
    hit = _find(res, "opportunities", "pb_absolute")
    assert hit["weight"] == 2 and "0.88x" in hit["evidence"]


def test_dividend_yield_only_fires_when_it_is_paid():
    res = W.build_swot(_bundle(overview={"Dividend Yield": "2.40 %"}))
    assert _find(res, "strengths", "dividend_yield")["weight"] == 2
    res = W.build_swot(_bundle(overview={"Dividend Yield": "0.00 %"}))
    assert _find(res, "strengths", "dividend_yield") is None


def test_micro_cap_size_is_a_threat():
    res = W.build_swot(_bundle(overview={"Market Cap": "₹ 121 Cr."}))
    assert _find(res, "threats", "market_cap")["weight"] == 1
    res = W.build_swot(_bundle(overview={"Market Cap": "₹ 26,360 Cr."}))
    assert _find(res, "threats", "market_cap") is None


# ================================================================== peers
def test_peer_beats_and_lags_on_returns():
    peers = {"roce": {"value": 21.3, "unit": "pct", "sector": 9.9}}
    res = W.build_swot(_bundle(**_health(peers=peers)))
    hit = _find(res, "strengths", "roce_vs_sector")
    assert hit["weight"] == 2 and hit["evidence"] == "ROCE 21.3% vs sector median 9.9%"

    peers = {"roe": {"value": 4.1, "unit": "pct", "sector": 14.0}}
    res = W.build_swot(_bundle(**_health(peers=peers)))
    assert _find(res, "weaknesses", "roe_vs_sector")["weight"] == 2
    assert _find(res, "threats", "competition_roe")["weight"] == 2


def test_peer_valuation_expensive_and_cheap():
    res = W.build_swot(_bundle(**_health(
        peers={"pe": {"value": 46.46, "unit": "x", "sector": 31.94}})))
    assert _find(res, "weaknesses", "pe_vs_sector")["weight"] == 2
    assert _find(res, "threats", "peer_derating")["weight"] == 2

    res = W.build_swot(_bundle(**_health(
        peers={"pb": {"value": 1.1, "unit": "x", "sector": 2.54}})))
    assert _find(res, "opportunities", "pb_vs_sector")["weight"] == 2


def test_nonsense_sector_medians_are_skipped():
    # Upstox sometimes reports a negative sector EV/EBITDA - never compare on it
    res = W.build_swot(_bundle(**_health(
        peers={"ev_ebitda": {"value": 32.34, "unit": "x", "sector": -41.5}})))
    assert _find(res, "weaknesses", "ev_ebitda_vs_sector") is None
    assert _find(res, "opportunities", "ev_ebitda_vs_sector") is None


def test_peers_fall_back_to_the_raw_upstox_block():
    bundle = _bundle()
    bundle["upstox_ratios"] = {"roce": {"value": 30.0, "unit": "pct", "sector": 10.0,
                                        "source": "upstox:key-ratios"}}
    res = W.build_swot(bundle)
    assert _find(res, "strengths", "roce_vs_sector")["weight"] == 2
    assert "analysis.health.peers" in res["coverage"]["inputs_used"]


# =========================================================== shareholding
def test_promoter_holding_level_and_direction():
    res = W.build_swot(_bundle(shareholding={
        "headers": QHDRS,
        "rows": {"Promoters": ["70.00%", "70.50%", "71.00%", "72.00%"]}}))
    assert _find(res, "strengths", "promoter_holding")["weight"] == 2
    add = _find(res, "strengths", "promoter_holding_change")
    assert add["weight"] == 2 and "70% (Sep 2025) -> 72% (Jun 2026)" in add["evidence"]
    assert _find(res, "opportunities", "promoter_buying") is not None


def test_low_and_falling_promoter_holding():
    res = W.build_swot(_bundle(shareholding={
        "headers": QHDRS,
        "rows": {"Promoters": ["24.00%", "23.00%", "22.00%", "20.00%"]}}))
    assert _find(res, "weaknesses", "promoter_holding")["weight"] == 3
    assert _find(res, "weaknesses", "promoter_holding_change")["weight"] == 2
    assert _find(res, "threats", "promoter_selling")["weight"] == 2


def test_institutions_building_and_exiting():
    res = W.build_swot(_bundle(shareholding={
        "headers": QHDRS,
        "rows": {"FIIs": ["3.00%", "3.20%", "3.60%", "4.10%"],
                 "DIIs": ["8.00%", "8.40%", "9.00%", "9.30%"]}}))
    assert _find(res, "opportunities", "fii_holding")["weight"] == 1
    assert _find(res, "opportunities", "dii_holding")["weight"] == 1
    assert _find(res, "threats", "institutional_exit") is None

    res = W.build_swot(_bundle(shareholding={
        "headers": QHDRS,
        "rows": {"FIIs": ["4.10%", "3.60%", "3.20%", "3.00%"],
                 "DIIs": ["9.30%", "9.00%", "8.40%", "8.00%"]}}))
    assert _find(res, "threats", "institutional_exit")["weight"] == 2


def test_money_flow_label_both_ways():
    res = W.build_swot(_bundle(analysis={"money_flow": {
        "label": "POSITIVE MONEY FLOW", "change": 0.42}}))
    assert _find(res, "strengths", "money_flow")["weight"] == 2
    assert _find(res, "opportunities", "institutional_flow")["weight"] == 2

    res = W.build_swot(_bundle(analysis={"money_flow": {
        "label": "NEGATIVE MONEY FLOW", "change": -0.04}}))
    hit = _find(res, "weaknesses", "money_flow")
    assert hit["weight"] == 2 and "-0.04 pp" in hit["evidence"]


def test_money_flow_without_a_number_is_not_a_point():
    res = W.build_swot(_bundle(analysis={"money_flow": {
        "label": "POSITIVE MONEY FLOW", "note": "no change value"}}))
    assert _find(res, "strengths", "money_flow") is None


def test_pledge_is_read_from_a_screener_con():
    res = W.build_swot(_bundle(cons=["Promoters have pledged 52.0% of their holding."]))
    weak = _find(res, "weaknesses", "promoter_pledge")
    threat = _find(res, "threats", "promoter_pledge")
    assert weak["weight"] == 3 and threat["weight"] == 3
    assert "52.0%" in weak["evidence"]


def test_zero_pledge_is_a_strength_only_when_stated():
    res = W.build_swot(_bundle(shareholding={"headers": HDRS,
                                             "rows": {"Pledged": ["0%", "0%", "0%"]}}))
    assert _find(res, "strengths", "promoter_pledge")["weight"] == 2
    # ... and absence of a pledge datum never invents "zero pledge"
    res = W.build_swot(_bundle(cons=["Stock is trading at 7.82 times its book value"]))
    assert _find(res, "strengths", "promoter_pledge") is None
    assert _find(res, "weaknesses", "promoter_pledge") is None


# =========================================================== price / risk
def test_technical_strength_and_weakness():
    res = W.build_swot(_bundle(prices={"technical": {
        "above_50dma": True, "above_200dma": True, "golden_cross": True,
        "rs_rating": 96, "pos_52w": 88.0, "dist_52w_high": -5.0,
        "excess_12m": 25.0, "benchmark": "Nifty 500"}}))
    assert _find(res, "strengths", "moving_averages")["weight"] == 2
    assert _find(res, "strengths", "golden_cross")["weight"] == 1
    assert _find(res, "strengths", "rs_rating")["weight"] == 2
    assert _find(res, "strengths", "pos_52w")["weight"] == 2
    assert _find(res, "strengths", "relative_return")["weight"] == 1

    res = W.build_swot(_bundle(prices={"technical": {
        "above_50dma": False, "above_200dma": False, "rs_rating": 12,
        "pos_52w": 8.0, "dist_52w_high": -62.0, "excess_12m": -41.0}}))
    assert _find(res, "weaknesses", "moving_averages")["weight"] == 2
    assert _find(res, "weaknesses", "rs_rating")["weight"] == 2
    assert _find(res, "weaknesses", "dist_52w_high")["weight"] == 2
    assert _find(res, "weaknesses", "relative_return")["weight"] == 2


def test_technical_rule_silent_without_the_price_block():
    res = W.build_swot(_bundle(prices={"ok": False}))
    assert res["strengths"] == [] and res["weaknesses"] == []
    assert "prices.technical" in res["coverage"]["inputs_missing"]


def test_drawdown_and_volatility_are_threats():
    res = W.build_swot(_bundle(prices={"risk": {"max_drawdown": -84.2,
                                                "ann_vol": 58.4, "sharpe": -0.3}}))
    assert _find(res, "threats", "max_drawdown")["weight"] == 3
    assert _find(res, "threats", "ann_vol")["weight"] == 2
    assert _find(res, "threats", "sharpe")["weight"] == 2

    res = W.build_swot(_bundle(prices={"risk": {"max_drawdown": -12.0,
                                                "ann_vol": 18.0, "sharpe": 1.4}}))
    assert _find(res, "threats", "max_drawdown") is None
    assert _find(res, "strengths", "sharpe")["weight"] == 1


# =============================================================== cyclical
def test_cyclical_pattern_splits_across_three_quadrants():
    res = W.build_swot(_bundle(analysis={"cyclical": {
        "label": "CYCLICAL", "positive_quarters": ["Mar", "Jun"],
        "negative_quarters": ["Sep", "Dec"]}}))
    assert _find(res, "weaknesses", "cyclical")["weight"] == 1
    assert "Mar, Jun" in _find(res, "opportunities", "cyclical_positive")["evidence"]
    assert "Sep, Dec" in _find(res, "threats", "cyclical_negative")["evidence"]


def test_non_cyclical_company_emits_no_cyclical_weakness():
    res = W.build_swot(_bundle(analysis={"cyclical": {
        "label": "NOT CYCLICAL", "positive_quarters": [],
        "negative_quarters": []}}))
    assert res["weaknesses"] == [] and res["opportunities"] == [] and res["threats"] == []


def test_growth_insight_labels():
    res = W.build_swot(_bundle(analysis={"growth_insight": {
        "label": "FUNDAMENTALS-LED",
        "long": "Profit grew 10% vs price 5% (5Y) - fundamentals outpacing the stock."}}))
    hit = _find(res, "strengths", "growth_insight")
    assert hit["weight"] == 2 and hit["evidence"].startswith("Profit grew 10%")

    res = W.build_swot(_bundle(analysis={"growth_insight": {
        "label": "PRICE-LED", "long": "Price grew 40% vs profit 5% (5Y)."}}))
    assert _find(res, "weaknesses", "growth_insight")["weight"] == 3
    assert _find(res, "threats", "price_led_derating")["weight"] == 2


# ================================================================= signal
def test_signal_blocks_and_result_metrics():
    res = W.build_swot(_bundle(signal={
        "label": "BUY", "composite": 87, "confidence": "Medium",
        "blocks": {"results": {"score": 97,
                               "reasons": ["Sales +1% YoY", "Net profit +21% YoY"],
                               "metrics": {"sales_yoy": 18.0, "np_yoy": 20.6,
                                           "np_qoq": -4.0, "accel": 7.3,
                                           "opm_exp": 1.0}}}}))
    hit = _find(res, "strengths", "results_score")
    assert hit["weight"] == 3 and "Results score 97/100" in hit["evidence"]
    assert _find(res, "strengths", "np_yoy")["weight"] == 3
    assert _find(res, "strengths", "sales_yoy")["weight"] == 2
    assert _find(res, "strengths", "earnings_accel")["weight"] == 2
    assert _find(res, "strengths", "opm_expansion")["weight"] == 2
    assert _find(res, "weaknesses", "np_qoq")["weight"] == 1
    assert _find(res, "strengths", "signal_label")["weight"] == 2


def test_weak_signal_blocks_read_as_weaknesses():
    res = W.build_swot(_bundle(signal={
        "label": "SELL", "composite": 21,
        "blocks": {"results": {"score": 12, "reasons": ["profit down YoY"],
                               "metrics": {"np_yoy": -33.0, "sales_yoy": -8.0}}}}))
    assert _find(res, "weaknesses", "results_score")["weight"] == 3
    assert _find(res, "weaknesses", "np_yoy")["weight"] == 3
    assert _find(res, "weaknesses", "sales_yoy")["weight"] == 2
    assert _find(res, "weaknesses", "signal_label")["weight"] == 2
    assert _find(res, "strengths", "results_score") is None


def test_signal_reasons_and_bias_flags_carry_over():
    res = W.build_swot(_bundle(signal={
        "reasons_pos": ["profit up QoQ", "margins expanding"],
        "reasons_neg": ["institutional outflow"],
        "bias_check": {"flags": [
            {"level": "warn", "title": "Valuation blind-spot",
             "note": "Rich valuation - P/E 46, DCF overvalued 100%."},
            {"level": "info", "title": "Size it", "note": "Cap the position."}]}}))
    assert [i["point"] for i in res["strengths"]] == ["Profit up QoQ.",
                                                      "Margins expanding."]
    flag = _find(res, "weaknesses", "bias_check")
    assert flag["weight"] == 3 and "Valuation blind-spot" in flag["point"]
    # 'info' level flags are coaching, not company evidence
    assert len([i for i in res["weaknesses"] if i["metric"] == "bias_check"]) == 1


# ======================================================== screener pros/cons
def test_screener_pros_and_cons_come_through_verbatim():
    res = W.build_swot(_bundle(
        pros=["Company is almost debt free.", "Debtors reduced to 40 days"],
        cons=["Stock is trading at 7.82 times its book value",
              "The company has delivered a poor sales growth of 3.81%"]))
    strong = _find(res, "strengths", "flagged_pro")
    assert strong["point"] == "Company is almost debt free."
    assert strong["evidence"] == "Flagged as a positive: Company is almost debt free."
    assert strong["weight"] == 2                       # 'debt free' is a strong pro
    minor = [i for i in res["strengths"] if i["metric"] == "flagged_pro"][1]
    assert minor["weight"] == 1
    assert any(i["weight"] == 2 for i in res["weaknesses"]
               if "poor sales growth" in i["point"])


def test_screener_cons_can_raise_a_threat_too():
    res = W.build_swot(_bundle(cons=[
        "Company has high contingent liabilities of Rs.240 Cr.",
        "Company has a low interest coverage ratio."]))
    assert _find(res, "threats", "contingent_liabilities")["weight"] == 2
    assert _find(res, "threats", "interest_coverage")["weight"] == 2


def test_no_pros_or_cons_means_no_text_points():
    res = W.build_swot(_bundle(pros=[], cons=["   "]))
    assert _find(res, "strengths", "flagged_pro") is None
    assert _find(res, "weaknesses", "flagged_con") is None


# ================================================================= sector
def test_sector_tailwind_and_headwind_in_both_row_shapes():
    res = W.build_swot(_bundle(), sector={"name": "Chemicals",
                                          "label": "TAILWIND", "score": 0.77})
    assert _find(res, "strengths", "sector_tailwind")["weight"] == 2
    hit = _find(res, "opportunities", "sector_tailwind")
    assert hit["evidence"] == "Chemicals: TAILWIND (score 0.77)"

    res = W.build_swot(_bundle(), sector={"sector": "Auto Components",
                                          "signal": "HEADWIND", "score": -0.62})
    assert _find(res, "weaknesses", "sector_headwind")["weight"] == 2
    assert _find(res, "threats", "sector_headwind")["weight"] == 3


def test_neutral_or_absent_sector_emits_nothing():
    for sector in (None, {}, {"name": "Textiles", "label": "NEUTRAL", "score": 0.1}):
        res = W.build_swot(_bundle(), sector=sector)
        assert _find(res, "strengths", "sector_tailwind") is None
        assert _find(res, "weaknesses", "sector_headwind") is None


# ================================================================ filings
def _filings(**themes):
    return {"code": "TEST",
            "analysis": {"themes": themes, "management_commitments": []}}


def test_filing_themes_become_opportunities_with_their_quote():
    filings = _filings(
        guidance=[_fact("Management expects 20% revenue growth in FY27.",
                        "We expect revenue to grow 20% in FY27.")],
        capex_expansion=[_fact("A new 500 kWp solar plant is under installation.",
                               "The Company is installing a 500kWp solar project.")],
        demand_outlook=[_fact("Demand for tractors is rising.",
                              "The demand for tractors is rising.")],
        orders_capacity=[_fact("Installed capacity rose to 1,200 buses a month.",
                               "increasing installed capacity to 1,200 buses")])
    res = W.build_swot(_bundle(), filings=filings)
    assert _find(res, "opportunities", "filing_guidance")["weight"] == 3
    assert _find(res, "opportunities", "filing_capex")["weight"] == 3
    demand = _find(res, "opportunities", "filing_demand")
    assert demand["weight"] == 2
    assert '"The demand for tractors is rising."' in demand["evidence"]
    assert "annual report - 2026-03-31" in demand["evidence"]
    assert _find(res, "opportunities", "filing_orders")["weight"] == 2
    assert "filings" in res["coverage"]["inputs_used"]


def test_ungrounded_filing_facts_are_dropped():
    filings = _filings(guidance=[{"claim": "Management expects 20% growth.",
                                  "doc_kind": "annual_report"}])
    res = W.build_swot(_bundle(), filings=filings)
    assert res["opportunities"] == []


def test_only_two_facts_per_theme_are_kept():
    filings = _filings(demand_outlook=[
        _fact("Demand alpha is rising.", "Demand alpha is rising."),
        _fact("Volumes in beta markets grew.", "Volumes in beta markets grew."),
        _fact("Exports to gamma improved.", "Exports to gamma improved.")])
    res = W.build_swot(_bundle(), filings=filings)
    assert len([i for i in res["opportunities"]
                if i["metric"] == "filing_demand"]) == 2


def test_management_commitments_carry_their_timeframe():
    filings = {"analysis": {"themes": {}, "management_commitments": [
        _fact("The company targeted zero landfill waste.",
              "The Company has targeted to reduce the waste sent to landfills.",
              timeframe="FY 2025-26")]}}
    res = W.build_swot(_bundle(), filings=filings)
    hit = _find(res, "opportunities", "filing_commitment")
    assert hit["weight"] == 2 and "[FY 2025-26]" in hit["evidence"]


def test_filing_risks_are_threats_and_governance_flags_are_decisive():
    filings = _filings(risks_headwinds=[
        _fact("Commodity prices remained volatile.",
              "Commodity and energy prices remained volatile."),
        _fact("The auditor issued a qualified opinion on the accounts.",
              "The auditor has issued a qualified opinion."),
        _fact("Revenue depends on a few customers.",
              "There is a concentration of revenue among top customers.")])
    res = W.build_swot(_bundle(), filings=filings)
    assert _find(res, "threats", "filing_risk")["weight"] == 2
    assert _find(res, "threats", "filing_governance_risk")["weight"] == 3
    assert _find(res, "threats", "filing_concentration_risk")["weight"] == 3


def test_no_filings_argument_means_no_filing_points():
    res = W.build_swot(_bundle(overview={"ROCE": "22 %"}))
    assert res["opportunities"] == []
    assert "filings" in res["coverage"]["inputs_missing"]


# ============================================ ordering, dedupe, cap, evidence
def test_quadrants_are_sorted_by_weight_descending():
    res = W.build_swot(_bundle(
        overview={"ROCE": "25 %", "Dividend Yield": "0.50 %"},
        **_health(current_ratio={"value": 2.4}, debt_equity={"value": 0.01})))
    weights = [i["weight"] for i in res["strengths"]]
    assert weights == sorted(weights, reverse=True)
    assert weights[0] == 3 and weights[-1] == 1


def test_exact_duplicate_points_are_collapsed():
    res = W.build_swot(_bundle(pros=["Company is almost debt free.",
                                     "Company is almost debt free!"]))
    assert len([i for i in res["strengths"] if i["metric"] == "flagged_pro"]) == 1


def test_near_identical_points_collapse_keeping_the_heavier_reading():
    kept = W._finalise([
        W.Point("Company is almost debt free.", "Flagged as a positive: A", "flagged_pro", 1),
        W.Point("The company is virtually debt free.", "Flagged as a positive: B",
                "flagged_pro", 3)])
    assert len(kept) == 1
    assert kept[0].weight == 3 and kept[0].evidence == "Flagged as a positive: B"


def test_computed_rules_that_read_alike_are_not_collapsed():
    kept = W._finalise([
        W.Point("Sales compounding has been strong over five years.", "e1",
                "sales_cagr_5y", 2),
        W.Point("Profit compounding has been strong over five years.", "e2",
                "profit_cagr_5y", 3)])
    assert len(kept) == 2


def test_a_single_shot_metric_never_appears_twice_in_one_quadrant():
    kept = W._finalise([W.Point("Interest cover is thin.", "e1",
                                "interest_coverage", 2),
                        W.Point("A low interest coverage ratio is a risk.", "e2",
                                "interest_coverage", 3)])
    assert len(kept) == 1 and kept[0].weight == 3


def test_repeatable_metrics_may_appear_more_than_once():
    kept = W._finalise([W.Point("Company is almost debt free.", "e1",
                                "flagged_pro", 2),
                        W.Point("Debtors reduced to 40 days.", "e2",
                                "flagged_pro", 1)])
    assert len(kept) == 2


def test_each_quadrant_is_capped_at_twelve_keeping_the_heaviest():
    pros = ["Company is almost debt free.",
            "Company has been maintaining a healthy dividend payout of 34.5%",
            "Company has delivered good profit growth of 22.1% CAGR over 5 years",
            "Company has a good return on equity (ROE) track record",
            "Debtors have reduced from 120 days to 80 days",
            "Company has been reducing debt.",
            "Improving cash conversion cycle across the group",
            "Promoter holding has increased by 1.60% over last quarter."]
    res = W.build_swot(_bundle(
        overview={"ROCE": "25 %", "ROE": "22 %", "Dividend Yield": "0.50 %"},
        pros=pros,
        **_health(debt_equity={"value": 0.01}, current_ratio={"value": 2.4})))
    assert len(res["strengths"]) == W.MAX_PER_QUADRANT == 12
    weights = [i["weight"] for i in res["strengths"]]
    assert weights == sorted(weights, reverse=True)
    assert weights.count(3) == 3                     # roce, roe, debt_equity
    # the lightest candidate is what got dropped
    assert all(i["point"] != pros[-1] for i in res["strengths"])


def test_a_point_is_never_emitted_without_its_evidence():
    bag = W._Bag()
    bag.s("A point with no datum", "", "roce", 3)
    bag.w("", "an orphan datum", "roce", 3)
    bag.o("A point", "a datum", "", 2)
    bag.t(None, None, "roce", 2)
    assert bag.strengths == [] and bag.weaknesses == []
    assert bag.opportunities == [] and bag.threats == []


def test_weights_are_clamped_into_the_one_to_three_band():
    bag = W._Bag()
    bag.s("Too heavy.", "datum", "roce", 9)
    bag.s("Too light.", "datum", "roe", 0)
    assert [p.weight for p in bag.strengths] == [3, 1]


def test_every_point_of_a_rich_bundle_is_well_formed():
    res = W.build_swot(_rich_bundle(),
                       sector={"name": "Chemicals", "label": "TAILWIND", "score": 0.77},
                       filings=_filings(guidance=[_fact(
                           "Management expects 20% revenue growth.",
                           "We expect revenue to grow 20%.")]))
    seen_any = False
    for quad in ("strengths", "weaknesses", "opportunities", "threats"):
        assert len(res[quad]) <= W.MAX_PER_QUADRANT
        weights = [i["weight"] for i in res[quad]]
        assert weights == sorted(weights, reverse=True)
        points = [i["point"].lower() for i in res[quad]]
        assert len(points) == len(set(points))
        for item in res[quad]:
            seen_any = True
            assert set(item) == {"point", "evidence", "metric", "weight"}
            assert item["point"].strip() and item["evidence"].strip()
            assert item["metric"].strip() and item["weight"] in (1, 2, 3)
            assert item["point"].endswith((".", "!", "?"))
    assert seen_any


def test_score_is_the_sum_of_the_kept_weights():
    res = W.build_swot(_rich_bundle())
    for quad, key in (("strengths", "s"), ("weaknesses", "w"),
                      ("opportunities", "o"), ("threats", "t")):
        assert res["score"][key] == sum(i["weight"] for i in res[quad])


# ================================================================ verdict
def test_verdict_names_the_dominant_quadrant():
    good = W.build_swot(_bundle(
        overview={"ROCE": "28 %", "ROE": "24 %"},
        **_health(debt_equity={"value": 0.01}, ocf_np={"value": 1.4})))
    assert good["verdict"].startswith("Strengths dominate the evidence")
    assert "S" in good["verdict"] and "/W" in good["verdict"]

    bad = W.build_swot(_bundle(
        overview={"ROCE": "2 %", "ROE": "1 %"},
        **_health(debt_equity={"value": 3.0}, ocf_np={"value": -0.5})))
    assert bad["verdict"].startswith("Weaknesses dominate the evidence")


def test_verdict_quotes_the_leading_point():
    res = W.build_swot(_bundle(overview={"ROCE": "28 %"}))
    assert res["strengths"][0]["point"].rstrip(".") in res["verdict"]


def test_verdict_is_honest_about_a_tie():
    res = W.build_swot(_bundle(overview={"ROCE": "28 %", "ROE": "2 %"}))
    assert res["score"]["s"] == res["score"]["w"] == 3
    assert res["verdict"].startswith("No quadrant dominates")
    assert "strengths, weaknesses" in res["verdict"]


# ================================================================== purity
def test_build_swot_is_pure_and_repeatable():
    bundle = _rich_bundle()
    snapshot = copy.deepcopy(bundle)
    first = W.build_swot(bundle, sector={"name": "Chemicals", "label": "TAILWIND"})
    second = W.build_swot(bundle, sector={"name": "Chemicals", "label": "TAILWIND"})
    assert first == second
    assert bundle == snapshot


# ------------------------------------------------------------- rich fixture
def _rich_bundle():
    """One bundle that lights up most rule families at once."""
    return _bundle(
        overview={"Market Cap": "₹ 26,360 Cr.", "Current Price": "₹ 528",
                  "High / Low": "₹ 665 / 363", "Stock P/E": "46.1",
                  "Book Value": "₹ 67.8", "Dividend Yield": "0.24 %",
                  "ROCE": "23.5 %", "ROE": "17.6 %", "Face Value": "₹ 10.0"},
        growth={"Compounded Sales Growth": {"10 Years": "16%", "5 Years": "4%",
                                            "3 Years": "-3%", "TTM": "-3%"},
                "Compounded Profit Growth": {"10 Years": "24%", "5 Years": "10%",
                                             "3 Years": "3%", "TTM": "3%"},
                "Stock Price CAGR": {"10 Years": "%", "5 Years": "5%",
                                     "3 Years": "10%", "1 Year": "-7%"},
                "Return on Equity": {"10 Years": "20%", "5 Years": "20%",
                                     "3 Years": "17%", "Last Year": "18%"}},
        profit_loss={"headers": ["Mar 2023", "Mar 2024", "Mar 2025", "Mar 2026"],
                     "rows": {"Sales": ["2,600", "2,900", "3,090", "3,186"],
                              "Expenses": ["2,200", "2,400", "2,467", "2,520"],
                              "Operating Profit": ["400", "500", "623", "666"],
                              "OPM %": ["15%", "17%", "20%", "21%"],
                              "Interest": ["5", "6", "6", "7"],
                              "Net Profit": ["300", "400", "502", "543"],
                              "Dividend Payout %": ["12%", "12%", "12%", "12%"]}},
        balance_sheet=_stmt({"Borrowings": ["25", "30", "53"],
                             "Equity Capital": ["50", "50", "50"],
                             "Reserves": ["2,800", "3,000", "3,340"],
                             "CWIP": ["30", "28", "20"]}),
        cash_flow=_stmt({"Cash from Operating Activity": ["756", "458", "443"],
                         "Free Cash Flow": ["695", "431", "404"],
                         "CFO/OP": ["187%", "100%", "91%"]}),
        ratios=_stmt({"Debtor Days": ["91", "90", "81"],
                      "Inventory Days": ["135", "154", "166"],
                      "Working Capital Days": ["173", "183", "155"],
                      "Cash Conversion Cycle": ["131", "136", "161"],
                      "ROCE %": ["21%", "25%", "23%"]}),
        shareholding={"headers": QHDRS,
                      "rows": {"Promoters": ["75.00%", "75.00%", "75.00%", "75.00%"],
                               "FIIs": ["3.37%", "3.41%", "3.02%", "3.02%"],
                               "DIIs": ["8.65%", "8.98%", "9.33%", "9.33%"]}},
        pros=["Company is almost debt free.",
              "Company has been maintaining a healthy dividend payout of 34.5%"],
        cons=["Stock is trading at 7.82 times its book value",
              "The company has delivered a poor sales growth of 3.81%"],
        analysis={
            "trends": {"yearly": {"Sales": {"label": "Increasing", "n": 4,
                                            "unit": "yrs"},
                                  "Net Profit": {"label": "Increasing", "n": 4,
                                                 "unit": "yrs"}},
                       "quarterly": {"OPM%": {"label": "Inconsistent", "n": 3,
                                              "unit": "qtrs"}}},
            "cyclical": {"label": "CYCLICAL", "positive_quarters": ["Mar", "Jun"],
                         "negative_quarters": ["Sep", "Dec"]},
            "growth_insight": {"label": "FUNDAMENTALS-LED",
                               "long": "Profit grew 10% vs price 5% (5Y)."},
            "money_flow": {"label": "NEGATIVE MONEY FLOW", "change": -0.04},
            "dcf": {"ok": True, "intrinsic_per_share": 264, "current_price": 528.0,
                    "margin_of_safety": -99.8,
                    "reverse": {"implied_growth": 19.51}},
            "health": {"current_ratio": {"value": 3.44, "year": "history"},
                       "ocf_np": {"value": 0.82, "year": "Mar 2026"},
                       "debt_equity": {"value": 0.02, "year": "Mar 2026"},
                       "cwip": {"latest": 20.0, "prev": 28.0, "pct_change": -28.6,
                                "year": "Mar 2026"},
                       "peers": {"pe": {"value": 46.46, "unit": "x", "sector": 31.94},
                                 "roce": {"value": 21.3, "unit": "pct",
                                          "sector": 9.9}}}},
        prices={"ok": True,
                "risk": {"max_drawdown": -44.12, "ann_vol": 34.19, "sharpe": 0.56},
                "technical": {"above_50dma": True, "above_200dma": True,
                              "golden_cross": True, "rs_rating": 96,
                              "pos_52w": 57.27, "dist_52w_high": -18.85,
                              "excess_12m": -8.27, "benchmark": "Nifty 500"}},
        signal={"label": "BUY", "composite": 87, "confidence": "Medium",
                "blocks": {"results": {"score": 97,
                                       "reasons": ["Sales +1% YoY",
                                                   "Net profit +21% YoY"],
                                       "metrics": {"sales_yoy": 0.6, "np_yoy": 20.6,
                                                   "np_qoq": 95.5, "accel": 7.3,
                                                   "opm_exp": 1.0}},
                           "technical": {"score": 100, "reasons": ["RS rating 96"]},
                           "fundamental": {"score": 65, "reasons": ["ROCE 24%"]}},
                "reasons_pos": ["profit growth >20% YoY"],
                "reasons_neg": ["DCF overvalued (-100%)"],
                "bias_check": {"flags": [{"level": "warn",
                                          "title": "Valuation blind-spot",
                                          "note": "Rich valuation - P/E 46."}]}})
