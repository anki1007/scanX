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
    bare = code.find('company/{code}/", timeout')
    assert cons > 0, "the consolidated URL is never requested"
    assert cons < bare, "standalone is still requested first"


def test_standalone_remains_the_fallback():
    """A company with no subsidiaries publishes no consolidated statement and
    that URL 404s — it must not become an error."""
    code = _code()
    assert 'company/{code}/", timeout' in code, "standalone fallback was removed"
    assert code.count("status_code != 200") >= 2, "the fallback chain is gone"


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


def test_headline_pe_prefers_the_key_ratios_feed():
    """Measured on 20 large caps: the feed and the scraped consolidated
    statement agree to a median 7.2%. Where they split, the feed is the sane
    one (ADANIENT 54.5 against a scraped 172). The feed is also dated daily.
    """
    html = PAGE.read_text(encoding="utf-8")
    assert "const _upe" in html, "the headline P/E override is gone"
    assert html.count("const _upe") == 1, "duplicated override block"
    assert "ov['Stock P/E'] = _upe" in html


def test_override_runs_before_the_card_list_is_built():
    """Otherwise a company the scrape missed shows no P/E card at all, even
    though the feed has one."""
    html = PAGE.read_text(encoding="utf-8")
    assert html.index("const _upe") < html.index("const keys=order.filter")
