"""Trailing P/E from filed quarters.

Cases are real ones, taken from the verification run against primary filings.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data.pe import (  # noqa: E402
    from_bundle, trailing_pe, ttm_eps,
)


# ---------------------------------------------------------------- real cases

def test_reliance_matches_the_filed_quarters():
    """13.42 + 13.78 + 12.54 + 15.48 = 55.22, and 1308/55.22 = 23.7.
    An independent reference put Reliance's trailing EPS at 59.7 against a
    price of 1394, i.e. 23.4 -- the same answer."""
    out = trailing_pe(1308, ["14.34", "19.95", "13.42", "13.78", "12.54", "15.48"])
    assert out["ttm_eps"] == 55.22
    assert out["value"] == 23.69


def test_hindunilvr_where_the_scraped_headline_read_44_8():
    """Filings say 33.0. The statement page headline said 44.8."""
    out = trailing_pe(2101, ["11.43", "28.12", "12.73", "11.38"])
    assert out["value"] == 33.0


def test_jswsteel_exceptional_quarter_is_flagged_not_hidden():
    """19,243 Cr in one quarter against a ~2,000 Cr norm. The reported trailing
    P/E of 12.5 is correct by convention -- and will not survive four more
    quarters, so it is flagged."""
    eps = ["6.15", "8.93", "6.64", "8.75", "66.94", "19.02"]
    out = trailing_pe(1270, eps)
    assert out["value"] == 12.53
    assert out["exceptional"] is True


def test_an_ordinary_company_is_not_flagged_exceptional():
    out = trailing_pe(2101, ["11.43", "28.12", "12.73", "11.38", "11.9", "12.1"])
    assert out["exceptional"] is False


# ------------------------------------------------------------- the two gaps

def test_loss_making_is_not_a_number():
    """A negative multiple ranks a loss-maker as cheap. It once put 125
    companies at the top of a value screen."""
    out = trailing_pe(100, ["-2.0", "-3.0", "-1.0", "-4.0"])
    assert out["value"] is None
    assert out["reason"] == "loss-making"


def test_three_quarters_is_not_a_year():
    out = trailing_pe(100, ["1.0", "2.0", "3.0"])
    assert out["value"] is None
    assert out["reason"] == "insufficient"


def test_a_missing_price_is_reported_as_such():
    out = trailing_pe(None, ["1.0", "2.0", "3.0", "4.0"])
    assert out["value"] is None
    assert out["reason"] == "no-price"


# ---------------------------------------------------------------- provenance

def test_the_quarters_used_are_named():
    """A disputed P/E should be arguable against the filings, not the vendor."""
    out = trailing_pe(
        1308, ["14.34", "19.95", "13.42", "13.78", "12.54", "15.48"],
        ["Mar 2025", "Jun 2025", "Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026"])
    assert out["quarters_used"] == ["Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026"]


# ------------------------------------------------------------------ hygiene

def test_messy_cells_do_not_crash_or_silently_become_zero():
    out = ttm_eps(["", "-", None, "₹ 12.5", "3,1.0", "4.0", "5.0", "6.0"])
    assert out["value"] is not None
    assert out["n"] >= 4


def test_pure_functions_never_raise_on_junk():
    for junk in (None, [], ["x"], [{}], [[]], "not a list"):
        assert trailing_pe(100, junk)["value"] is None
    assert from_bundle(None)["value"] is None
    assert from_bundle({})["value"] is None
    assert from_bundle({"fundamental": []})["value"] is None


def test_from_bundle_reads_a_baked_shape():
    bundle = {"fundamental": {
        "overview": {"Current Price": "₹ 1,308"},
        "quarters": {"headers": ["Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026"],
                     "rows": {"Sales": ["1", "2", "3", "4"],
                              "EPS": ["13.42", "13.78", "12.54", "15.48"]}}}}
    out = from_bundle(bundle)
    assert out["value"] == 23.69
    assert out["quarters_used"][-1] == "Jun 2026"


# ------------------------------- the share-count trap that caught our method

def test_quarters_that_reconcile_with_the_filed_year_are_trustworthy():
    """JSWSTEEL's four FY26 quarters sum to 91.44 against a filed annual 91.43.
    That reconciliation is what licenses summing quarters at all."""
    from earnings_intel.data.pe import reconciles_with_annual
    assert reconciles_with_annual(["8.95", "6.66", "8.76", "67.07"], "91.43") is True


def test_a_mid_year_share_count_event_is_caught():
    """SHRIRAMFIN summed to 18.5 against a true 21.8 -- a 15% error, because
    quarterly EPS figures are each struck on that quarter's weighted-average
    share count and a rights issue lands between them."""
    from earnings_intel.data.pe import reconciles_with_annual
    assert reconciles_with_annual(["4.0", "4.5", "5.0", "5.0"], "21.8") is False


def test_nothing_to_compare_against_is_unknown_not_fine():
    from earnings_intel.data.pe import reconciles_with_annual
    assert reconciles_with_annual(["1", "2", "3", "4"], None) is None
    assert reconciles_with_annual(["1", "2"], "10") is None
    assert reconciles_with_annual(["1", "2", "3", "4"], "0") is None
