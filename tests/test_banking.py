"""Tests for the Banking Data board — comma-string parsing, YoY growth,
matrix assembly from fixture bundles, and missing-row tolerance.

Pure functions only: no file or network I/O.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import refresh_banking as rb   # noqa: E402


# ---------------------------------------------------------------- fixtures
def _bundle():
    """Mini bank bundle in the exact shape of docs/data/fundamental/<CODE>.json."""
    return {
        "fundamental": {
            "code": "TESTBANK", "name": "Test Bank Ltd",
            "profit_loss": {
                "headers": ["Mar 2023", "Mar 2024", "Mar 2025"],
                "rows": {
                    "Revenue": ["1,000", "1,200", "1,500"],
                    "Financing Margin %": ["10%", "12%", "15%"],
                    "Net Profit": ["100", "150", "180"],
                    "EPS in Rs": ["5.00", "7.50", "9.00"],
                },
            },
            "balance_sheet": {
                "headers": ["Mar 2023", "Mar 2024", "Mar 2025"],
                "rows": {"Total Assets": ["10,000", "12,000", "15,000"]},
            },
            "ratios": {
                "headers": ["Mar 2023", "Mar 2024", "Mar 2025"],
                "rows": {"ROE %": ["12%", "14%", "15%"]},
            },
            "quarters": {
                "headers": ["Jun 2024", "Sep 2024", "Dec 2024", "Mar 2025",
                            "Jun 2025", "Sep 2025", "Dec 2025", "Mar 2026"],
                "rows": {"Net Profit": ["40", "42", "44", "50",
                                        "44", "46", "48", "60"]},
            },
        },
        "prices": {}, "signal": {},
    }


# ---------------------------------------------------------------- parsing
def test_parse_num():
    assert rb.parse_num("48,470") == 48470.0
    assert rb.parse_num("-6,547") == -6547.0
    assert rb.parse_num("14%") == 14.0
    assert rb.parse_num("10.19") == 10.19
    assert rb.parse_num("₹ 772") == 772.0
    assert rb.parse_num(42) == 42.0
    assert rb.parse_num("") is None
    assert rb.parse_num("—") is None
    assert rb.parse_num(None) is None
    assert rb.parse_num("n/a") is None


def test_fy_labels():
    assert rb.is_fy_label("Mar 2015")
    assert not rb.is_fy_label("Mar 2017 9m")      # partial period dropped
    assert not rb.is_fy_label("Latest Qtr")
    assert rb.year_key("Mar 2015") == (2015, 3)
    assert rb.year_key("Dec 2024") == (2024, 12)
    assert rb.year_key("garbage") == (9999, 99)   # sorts last


# ---------------------------------------------------------------- growth
def test_growth_pct():
    assert rb.growth_pct(150.0, 100.0) == 50.0
    assert rb.growth_pct(50.0, -100.0) == 150.0   # off a loss, |prev| base
    assert rb.growth_pct(None, 100.0) is None
    assert rb.growth_pct(100.0, None) is None
    assert rb.growth_pct(100.0, 0.0) is None      # zero base


def test_yoy_map_skips_partial_and_gap_years():
    headers = ["Mar 2016", "Mar 2017 9m", "Mar 2018", "Mar 2019"]
    m = {"Mar 2016": 100.0, "Mar 2018": 200.0, "Mar 2019": 300.0}
    out = rb.yoy_map(m, headers)
    # Mar 2018 vs Mar 2016 is a 2-year gap once the 9m column drops -> no YoY
    assert "Mar 2018" not in out
    assert out["Mar 2019"] == 50.0


# ---------------------------------------------------------------- extract
def test_extract_bank_full():
    b = rb.extract_bank(_bundle(), "TESTBANK", "PRIVATE")
    assert b["code"] == "TESTBANK" and b["name"] == "Test Bank Ltd"
    assert b["group"] == "PRIVATE"
    s = b["series"]
    assert s["fin_margin"]["Mar 2025"] == 15.0
    assert s["np"]["Mar 2024"] == 150.0
    assert s["np_yoy"]["Mar 2024"] == 50.0        # 100 -> 150
    assert s["np_yoy"]["Mar 2025"] == 20.0        # 150 -> 180
    assert s["rev_yoy"]["Mar 2025"] == 25.0       # 1200 -> 1500
    assert s["eps"]["Mar 2025"] == 9.0
    assert s["roe"]["Mar 2025"] == 15.0
    assert s["roa"]["Mar 2025"] == 1.2            # 180 / 15000 * 100
    assert s["np_margin"]["Mar 2025"] == 12.0     # 180 / 1500 * 100
    # latest qtr Mar 2026 (60) vs Mar 2025 (50) -> +20%
    assert s["q_np_yoy"]["Latest Qtr"] == 20.0
    assert b["latest_q"] == "Mar 2026"


def test_extract_bank_missing_rows_tolerated():
    bundle = _bundle()
    f = bundle["fundamental"]
    del f["balance_sheet"]["rows"]["Total Assets"]   # no ROA inputs
    del f["ratios"]                                   # no ROE table at all
    f["quarters"]["rows"] = {}                        # no quarterly NP
    b = rb.extract_bank(bundle, "TESTBANK", "PSU")
    s = b["series"]
    assert s["roa"] == {} and s["roe"] == {} and s["q_np_yoy"] == {}
    assert s["np"]["Mar 2025"] == 180.0               # rest still works


def test_extract_bank_empty_bundle():
    b = rb.extract_bank({}, "GHOST", "NBFC")
    assert b["name"] == "GHOST"
    assert not any(b["series"].values())


# ---------------------------------------------------------------- matrices
def test_build_matrices_alignment_and_nulls():
    b1 = rb.extract_bank(_bundle(), "TESTBANK", "PRIVATE")
    bundle2 = _bundle()
    f2 = bundle2["fundamental"]
    f2["name"] = "Other Bank"
    # shift Other Bank one year back: Mar 2022..Mar 2024
    f2["profit_loss"]["headers"] = ["Mar 2022", "Mar 2023", "Mar 2024"]
    f2["balance_sheet"]["headers"] = ["Mar 2022", "Mar 2023", "Mar 2024"]
    f2["ratios"]["headers"] = ["Mar 2022", "Mar 2023", "Mar 2024"]
    b2 = rb.extract_bank(bundle2, "OTHERBANK", "PSU")

    mats = rb.build_matrices([b1, b2], max_years=10)
    by = {m["metric"]: m for m in mats}
    np_ = by["np"]
    assert np_["years"] == ["Mar 2022", "Mar 2023", "Mar 2024", "Mar 2025"]
    r1 = next(r for r in np_["banks"] if r["code"] == "TESTBANK")
    r2 = next(r for r in np_["banks"] if r["code"] == "OTHERBANK")
    assert r1["values"] == [None, 100.0, 150.0, 180.0]   # no Mar 2022
    assert r2["values"] == [100.0, 150.0, 180.0, None]   # no Mar 2025
    assert r2["group"] == "PSU" and r2["name"] == "Other Bank"
    # quarterly metric is a single synthetic column
    assert by["q_np_yoy"]["years"] == ["Latest Qtr"]
    # every shipped matrix carries label + unit for the page
    assert all(m["label"] and "unit" in m for m in mats)


def test_build_matrices_max_years_window():
    b = rb.extract_bank(_bundle(), "TESTBANK", "PRIVATE")
    mats = rb.build_matrices([b], max_years=2)
    by = {m["metric"]: m for m in mats}
    assert by["np"]["years"] == ["Mar 2024", "Mar 2025"]


def test_build_matrices_drops_empty_metrics_and_banks():
    bundle = _bundle()
    f = bundle["fundamental"]
    del f["ratios"]                                   # nobody has ROE
    b = rb.extract_bank(bundle, "TESTBANK", "PRIVATE")
    ghost = rb.extract_bank({}, "GHOST", "NBFC")      # no data at all
    mats = rb.build_matrices([b, ghost])
    keys = [m["metric"] for m in mats]
    assert "roe" not in keys                          # metric with no inputs skipped
    for m in mats:
        assert [r["code"] for r in m["banks"]] == ["TESTBANK"]   # ghost dropped
