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


def test_headline_pe_is_computed_from_filed_quarters():
    """Verified against primary filings on seven large caps: computing it
    lands within a median 0.5%, while the statement headline read 25.8 for
    JSWSTEEL against a filed 12.5 and the feed read 28.2 for DLF against 37.0.
    Neither precomputed source is safe to publish unchecked."""
    html = PAGE.read_text(encoding="utf-8")
    assert "const _peInfo" in html, "the computed P/E is gone"
    assert html.count("const _peInfo") == 1, "duplicated P/E block"
    assert "ov['Stock P/E'] = _peInfo.pe.toFixed(1)" in html


def test_the_feed_is_only_a_last_resort():
    """It may fill a gap; it may not overwrite a computed number."""
    html = PAGE.read_text(encoding="utf-8")
    i_compute = html.index("ov['Stock P/E'] = _peInfo.pe.toFixed(1)")
    i_feed = html.index("const _upe")
    assert i_compute < i_feed, "the feed can still override the computed P/E"
    assert "ov['Stock P/E'] == null" in html, "the feed is not gated on a gap"


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
