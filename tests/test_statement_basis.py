"""Which statement a ratio came from — standalone or consolidated.

Verified against BSE (the exchange) and an independent reference on 78 large
caps. The bare /company/<code>/ page is the STANDALONE statement, which for a
holding or conglomerate structure excludes the operating subsidiaries:

    GRASIM      standalone 500.0   consolidated ~42    BSE 15.0
    ADANIPORTS  standalone 141.0   consolidated ~29    BSE 16.9
    BAJAJFINSV  standalone 146.0   consolidated ~27    BSE 16.9

Eleven of 78 were more than 50% out, and every one was a holding structure.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SRC = ROOT / "earnings_intel" / "data" / "company.py"
PAGE = ROOT / "docs" / "fundamental.html"


def _code():
    """Source with comments stripped — a comment naming a URL is not a fetch."""
    text = SRC.read_text(encoding="utf-8")
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def test_consolidated_is_requested_before_standalone():
    code = _code()
    cons = code.find("/consolidated/")
    bare = code.find('company/{code}/"')
    assert cons > 0, "the consolidated URL is never requested"
    assert bare > 0, "the standalone URL is never requested"
    assert cons < bare, "standalone is still requested first"


def test_standalone_remains_the_fallback():
    """A company with no subsidiaries publishes no consolidated statement and
    that URL 404s — it must not become an error."""
    code = _code()
    assert 'company/{code}/"' in code, "standalone fallback was removed"
    assert "status_code == 404" in code,         "the fallback is no longer gated on 404 -- a rate limit would be absorbed again"


def test_the_basis_is_recorded_in_the_bundle():
    """A P/E without its basis is not a fact. The two differ by 10x."""
    code = _code()
    assert '"basis": basis' in code
    assert 'basis = "consolidated"' in code and 'basis = "standalone"' in code


def test_the_page_labels_which_basis_a_ratio_came_from():
    html = PAGE.read_text(encoding="utf-8")
    assert "BASIS_TAGGED" in html
    for field in ("Stock P/E", "ROCE", "ROE"):
        assert field in html


def test_the_feed_is_the_headline_pe():
    """Product decision: the key-ratios feed is the source of record for the
    headline multiple -- dated daily, no scraped login session, and it does not
    go wrong under rate limiting the way the statement scrape does.

    Its known errors are accepted, not overlooked: standalone on some holding
    structures, and "net profit for the period" rather than "attributable to
    owners" where minority interests are large (JSWSTEEL 11.1 against a filed
    12.5). This test exists so nobody quietly reverts the decision."""
    html = PAGE.read_text(encoding="utf-8")
    i_feed = html.index("ov['Stock P/E'] = _upe.toFixed(1)")
    i_computed = html.index("ov['Stock P/E'] = _peInfo.pe.toFixed(1)")
    assert i_feed < i_computed, "the computed value can still beat the feed"


def test_the_computed_value_fills_the_feeds_gaps():
    """The feed carries a P/E for 56% of bundles. Without a fallback, 1,540
    companies that have one today would go blank."""
    html = PAGE.read_text(encoding="utf-8")
    assert "const _peInfo" in html, "the computed P/E is gone"
    assert html.count("const _peInfo") == 1, "duplicated P/E block"
    assert "UPSTOX_ONLY" in html, "no switch for strict feed-only mode"


def test_a_filled_gap_is_labelled_not_silently_mixed():
    """A reader must be able to tell which number they are looking at."""
    html = PAGE.read_text(encoding="utf-8")
    assert "_peShown" in html
    assert "'· computed · '" in html or "· computed · " in html


def test_loss_making_shows_a_dash_not_a_negative_multiple():
    """A negative P/E ranks a loss-maker as cheap."""
    html = PAGE.read_text(encoding="utf-8")
    assert "_peInfo.loss" in html


def test_the_quarters_behind_the_pe_are_named():
    html = PAGE.read_text(encoding="utf-8")
    assert "peNote" in html and "_peInfo.quarters" in html
    assert "one-off" in html, "an exceptional quarter is not marked"


def test_computation_runs_before_the_card_list_is_built():
    """Otherwise a company the scrape missed shows no P/E card at all."""
    html = PAGE.read_text(encoding="utf-8")
    assert html.index("const _peInfo") < html.index("const keys=order.filter")
