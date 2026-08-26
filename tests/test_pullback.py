"""The pullback screen.

Fifteen conditions. The ones that bite here are the ones where a missing
number could quietly become a pass: about one company in eight is a bank or
an NBFC with no "Sales" and no "OPM" line, and volume did not exist in this
repo at all until the price layer started storing it. Both must read as
UNTESTED, never as satisfied.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.pullback import (  # noqa: E402
    CONDITIONS, evaluate, facts, passes,
)

DOCS = ROOT / "docs"


def _bundle(mcap="₹ 5,000 Cr.", price="₹ 250",
            q_sales=None, q_profit=None, q_opm=None,
            a_sales=None, a_profit=None, a_opm=None, headers=None,
            tech=None):
    """A bundle that passes everything unless an argument says otherwise."""
    q_sales = q_sales if q_sales is not None else [100, 100, 100, 100, 150]
    q_profit = q_profit if q_profit is not None else [10, 10, 10, 10, 30]
    q_opm = q_opm if q_opm is not None else ["20%", "20%", "20%", "20%", "25%"]
    a_sales = a_sales if a_sales is not None else ["400", "420"]
    a_profit = a_profit if a_profit is not None else ["40", "42"]
    a_opm = a_opm if a_opm is not None else ["10%", "10%"]
    t = {"above_50dma": True, "golden_cross": True, "ret_1m": 4.0,
         "ret_1w": -1.5, "vol_1w": 100.0, "vol_1m": 200.0, "vol_1y": 150.0}
    if tech is not None:
        t.update(tech)
    return {
        "fundamental": {
            "name": "Test Co",
            "overview": {"Market Cap": mcap, "Current Price": price},
            "quarters": {"headers": ["a", "b", "c", "d", "e"],
                         "rows": {"Sales": q_sales, "Net Profit": q_profit,
                                  "OPM": q_opm}},
            "profit_loss": {"headers": headers or ["Mar 2025", "Mar 2026"],
                            "rows": {"Sales": a_sales, "Net Profit": a_profit,
                                     "OPM %": a_opm}},
        },
        "prices": {"ok": True, "technical": t},
    }


# ------------------------------------------------------------- the baseline

def test_the_reference_company_passes_every_condition():
    v = evaluate(_bundle())
    assert v["pass"] and v["untested"] == [] and v["tested"] == 15, v


def test_there_are_exactly_the_fifteen_conditions_asked_for():
    assert len(CONDITIONS) == 15
    assert len({k for k, _ in CONDITIONS}) == 15


# --------------------------------------------------- each condition can fail

@pytest.mark.parametrize("kwargs,expect", [
    ({"mcap": "₹ 500 Cr."}, "mcap_over"),
    ({"mcap": "₹ 2,00,000 Cr."}, "mcap_under"),
    ({"a_sales": ["150", "160"]}, "sales_over"),
    ({"q_sales": [100, 100, 100, 100, 110]}, "q_sales_growth"),
    ({"q_profit": [10, 10, 10, 10, 12]}, "q_profit_growth"),
    ({"tech": {"above_50dma": False}}, "above_50dma"),
    ({"tech": {"golden_cross": False}}, "golden_cross"),
    ({"tech": {"ret_1m": -2.0}}, "up_1m"),
    ({"tech": {"ret_1w": 3.0}}, "down_1w"),
    ({"tech": {"vol_1w": 300.0}}, "vol_cooling"),
    ({"tech": {"vol_1y": 400.0}}, "vol_elevated"),
])
def test_a_broken_condition_fails_the_screen(kwargs, expect):
    v = evaluate(_bundle(**kwargs))
    assert expect in v["failed"], (expect, v["failed"])
    assert not v["pass"]


def test_profit_must_outgrow_sales():
    # sales +50%, profit +25%: both strong, but the wrong way round
    v = evaluate(_bundle(q_sales=[100, 100, 100, 100, 150],
                         q_profit=[100, 100, 100, 100, 125]))
    assert "profit_beats_sales" in v["failed"]


def test_margin_must_be_above_its_own_five_year_level():
    v = evaluate(_bundle(q_opm=["9%"] * 5, a_opm=["20%", "20%"]))
    assert "opm_expanding" in v["failed"]


def test_the_quarter_must_outrun_the_annual_trend():
    """Acceleration, not merely growth: a company already compounding at 80%
    a year is not accelerating when a quarter prints 50%."""
    v = evaluate(_bundle(a_sales=["100", "400"], a_profit=["10", "80"]))
    assert "sales_accel" in v["failed"] and "profit_accel" in v["failed"]


# ------------------------------------------- missing data is not a pass

def test_a_bank_with_no_sales_or_opm_line_is_untested_not_passed():
    """Banks report Financing Profit; there is no Sales and no OPM row at all.
    Those conditions must not silently succeed."""
    b = _bundle()
    del b["fundamental"]["quarters"]["rows"]["Sales"]
    del b["fundamental"]["quarters"]["rows"]["OPM"]
    del b["fundamental"]["profit_loss"]["rows"]["Sales"]
    v = evaluate(b)
    for key in ("q_sales_growth", "profit_beats_sales", "opm_expanding",
                "sales_accel", "sales_over"):
        assert key in v["untested"], key
        assert key not in v["failed"]
    assert v["tested"] < 15


def test_absent_volume_is_untested_not_satisfied():
    """The whole repo had no volume until recently. "1w < 1m" on two Nones
    must not evaluate to true."""
    v = evaluate(_bundle(tech={"vol_1w": None, "vol_1m": None, "vol_1y": None}))
    assert "vol_cooling" in v["untested"] and "vol_elevated" in v["untested"]
    assert "vol_cooling" not in v["failed"]


def test_a_bundle_with_no_price_block_leaves_the_price_conditions_untested():
    b = _bundle()
    del b["prices"]
    v = evaluate(b)
    for key in ("above_50dma", "golden_cross", "up_1m", "down_1w",
                "vol_cooling", "vol_elevated"):
        assert key in v["untested"], key


# ---------------------------------------------------------- reading numbers

def test_growth_is_year_on_year_not_quarter_on_quarter():
    """Seasonal businesses: June against March measures the season."""
    f = facts(_bundle(q_sales=[100, 5, 5, 5, 150]))
    assert f["q_sales_yoy"] == 50.0


def test_the_ttm_column_is_excluded_from_annual_growth():
    """profit_loss ends in TTM. Comparing it with the prior full year is a
    part year against a whole one, not a growth rate."""
    f = facts(_bundle(headers=["Mar 2025", "Mar 2026", "TTM"],
                      a_sales=["400", "420", "500"],
                      a_profit=["40", "42", "60"],
                      a_opm=["10%", "10%", "12%"]))
    assert f["sales_growth"] == pytest.approx(5.0)      # 400 -> 420, not 420 -> 500
    assert f["sales"] == 420.0


def test_a_loss_in_brackets_is_read_as_negative():
    """(4.2) is -4.2. Read as +4.2 it turns a loss into a profit and the
    growth rate flips sign."""
    f = facts(_bundle(q_profit=["(10)", "10", "10", "10", "30"]))
    # -10 -> 30 against a base of |-10|: a swing of 40 on 10, i.e. +400%.
    # Read as +4.2 instead, the base would be +10 and the answer +200%.
    assert f["q_profit_yoy"] == pytest.approx(400.0)


def test_quarterly_opm_is_found_under_its_own_label():
    """Quarters say "OPM", annuals say "OPM %". Matching one name loses the
    other and the margin condition silently goes untested."""
    f = facts(_bundle())
    assert f["q_opm"] == 25.0 and f["opm_5y"] == 10.0


def test_a_zero_base_does_not_produce_infinite_growth():
    f = facts(_bundle(q_sales=[0, 1, 1, 1, 40]))
    assert f["q_sales_yoy"] is None


def test_junk_never_raises():
    for junk in (None, {}, [], "x", 5, {"fundamental": []},
                 {"fundamental": {"quarters": "no"}},
                 {"fundamental": {"overview": "no"}, "prices": "no"}):
        v = evaluate(junk)
        assert v["pass"] is False or v["tested"] == 0
        assert isinstance(passes(junk), bool)


# ------------------------------------------------------------- the plumbing

def test_the_baker_is_scheduled():
    """A screen nothing refreshes freezes the day it ships."""
    wf = "\n".join(p.read_text(encoding="utf-8")
                   for p in (ROOT / ".github" / "workflows").glob("*.yml"))
    assert "refresh_pullback.py" in wf


def test_the_view_is_reachable_from_the_dropdown():
    html = (DOCS / "technofunda.html").read_text(encoding="utf-8")
    assert '<option value="PB">' in html
    assert "PB_COLS" in html and "loadPullback" in html
    assert "view==='PB'" in html


def test_the_baked_board_matches_the_module():
    """Skips on a fresh clone rather than demanding the data directory."""
    out = DOCS / "data" / "pullback.json"
    if not out.exists():
        pytest.skip("pullback.json not baked in this checkout")
    j = json.loads(out.read_text(encoding="utf-8"))
    assert j["conditions_total"] == len(CONDITIONS)
    assert j["passed"] == len(j["rows"])
    for r in j["rows"][:50]:
        assert r["code"] and r["tested"] <= len(CONDITIONS)
        # every row published is one that FAILED nothing
        assert set(r["untested"]) <= {k for k, _ in CONDITIONS}


def test_the_screen_is_documented_where_it_is_defined():
    """The fifteen conditions are the spec; they belong next to the code that
    applies them, not only in a chat message."""
    src = (ROOT / "earnings_intel" / "data" / "pullback.py").read_text(encoding="utf-8")
    for phrase in ("Market Capitalization > 1000", "OPM latest quarter > OPM 5Year",
                   "Volume 1week average < Volume 1month average"):
        assert phrase in src, phrase


def test_the_pullback_view_shows_a_live_price_and_day_change():
    """The screen's own columns. How the feed reaches them is the shared
    module's business -- tests/test_quotes_feed.py owns that."""
    html = (DOCS / "technofunda.html").read_text(encoding="utf-8")
    assert '["ltp","LTP","rs"],["pct","% Chg","chg"]' in html
    assert "scanXQuotes.apply(PB_DATA)" in html, (
        "the pullback rows load after the poll, so they must be priced from "
        "the cached feed or the whole column shows a dash")
