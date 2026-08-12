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


# ------------------ the empty consolidated page: 613 blank bundles shipped

def test_has_values_rejects_a_page_of_units():
    """Screener renders the ratio cards even with nothing to put in them, so a
    scrape comes back structurally perfect and semantically empty. Every key
    present, every value a non-empty string, every value useless."""
    from earnings_intel.data.company import _has_values
    empty = {"Market Cap": "\u20b9 Cr.", "ROCE": "%", "Stock P/E": "",
             "Book Value": "\u20b9", "High / Low": "\u20b9 /"}
    assert _has_values(empty) is False
    real = dict(empty, **{"Market Cap": "\u20b9 151 Cr."})
    assert _has_values(real) is True


def test_has_values_survives_junk():
    from earnings_intel.data.company import _has_values
    for junk in (None, {}, [], "x", {"a": None}, {"a": {}}):
        assert _has_values(junk) is False


def test_an_empty_consolidated_page_falls_back_to_standalone(monkeypatch):
    """A 404 is not the only way a company lacks a consolidated statement --
    most answer 200 with a page carrying labels and no numbers. Falling back
    only on 404 published 613 companies with every field blank."""
    import earnings_intel.data.company as C

    EMPTY = "<html><h1>X</h1></html>"
    FULL = "<html><h1>X</h1>full</html>"

    class _R:
        def __init__(self, text):
            self.status_code, self.text, self.headers = 200, text, {}

    asked = []

    def fake_get(url, session_id=None, timeout=None):
        asked.append(url)
        return _R(EMPTY if url.endswith("/consolidated/") else FULL)

    monkeypatch.setattr(C, "_retry_get", fake_get)
    monkeypatch.setattr(C, "_overview",
                        lambda soup: {"Market Cap": "\u20b9 Cr."}
                        if "full" not in str(soup) else {"Market Cap": "\u20b9 151 Cr."})
    for fn in ("_growth", "_quarters", "_statement", "_shareholding", "_insights", "_analyze"):
        if hasattr(C, fn):
            monkeypatch.setattr(C, fn, lambda *a, **k: {})
    C._FCACHE.clear()

    out = C.fundamentals("526971", timeout=5)
    assert out.get("basis") == "standalone", "kept the empty consolidated page"
    assert any(u.endswith("/consolidated/") for u in asked), "never tried consolidated"
    assert any(u.endswith("/company/526971/") for u in asked), "never fell back"


# The "a complete consolidated page is never thrown away" guarantee lives in
# test_an_equal_score_never_flips_a_holding_company below. An earlier version
# of it sat here and mocked only the overview, which quietly encoded the weaker
# rule -- under the stricter one, a consolidated page with ratios but NO
# quarterly table now correctly loses to standalone, because that exact shape
# cost 121 companies their charts.


# --------------- the partially-filled consolidated page: 121 lost their charts

def test_completeness_scores_both_halves_of_a_page():
    """Screener fills a consolidated page in DEGREES. Checking only the
    overview let 121 companies keep a page with no quarterly table while their
    standalone page had twelve -- losing the charts, the computed P/E and the
    debate's freshness fingerprint, all of which are built from quarters."""
    from earnings_intel.data.company import _completeness
    nothing = ({"Market Cap": "\u20b9 Cr."}, {"headers": []})
    partial = ({"Market Cap": "\u20b9 90 Cr."}, {"headers": []})
    full = ({"Market Cap": "\u20b9 90 Cr."}, {"headers": ["Mar 2026"]})
    assert _completeness(*nothing) == 0
    assert _completeness(*partial) == 1
    assert _completeness(*full) == 2


def test_a_consolidated_page_with_no_quarters_loses_to_standalone(monkeypatch):
    import earnings_intel.data.company as C

    class _R:
        def __init__(self, tag):
            self.status_code, self.text, self.headers = 200, f"<html><h1>{tag}</h1></html>", {}

    asked = []

    def fake_get(url, session_id=None, timeout=None):
        asked.append(url)
        return _R("cons" if url.endswith("/consolidated/") else "stand")

    def fake_quarters(soup, *a, **k):
        return {"headers": [] if "cons" in str(soup) else ["Mar 2026"], "rows": {}}

    monkeypatch.setattr(C, "_retry_get", fake_get)
    monkeypatch.setattr(C, "_overview", lambda soup: {"Market Cap": "\u20b9 90 Cr."})
    monkeypatch.setattr(C, "_quarters", fake_quarters)
    for fn in ("_growth", "_statement", "_shareholding", "_insights", "_analyze"):
        if hasattr(C, fn):
            monkeypatch.setattr(C, fn, lambda *a, **k: {})
    C._FCACHE.clear()

    out = C.fundamentals("504908", timeout=5)
    assert out.get("basis") == "standalone"
    assert out.get("quarters", {}).get("headers"), "kept the page with no quarters"


def test_an_equal_score_never_flips_a_holding_company(monkeypatch):
    """Both pages complete means consolidated wins. Flipping on a tie would
    undo the whole fix and hand Grasim back its standalone P/E of 500."""
    import earnings_intel.data.company as C

    class _R:
        def __init__(self):
            self.status_code, self.text, self.headers = 200, "<html><h1>X</h1></html>", {}

    asked = []

    def fake_get(url, session_id=None, timeout=None):
        asked.append(url)
        return _R()

    monkeypatch.setattr(C, "_retry_get", fake_get)
    monkeypatch.setattr(C, "_overview", lambda soup: {"Market Cap": "\u20b9 2,25,678 Cr."})
    monkeypatch.setattr(C, "_quarters", lambda *a, **k: {"headers": ["Mar 2026"], "rows": {}})
    for fn in ("_growth", "_statement", "_shareholding", "_insights", "_analyze"):
        if hasattr(C, fn):
            monkeypatch.setattr(C, fn, lambda *a, **k: {})
    C._FCACHE.clear()

    out = C.fundamentals("GRASIM", timeout=5)
    assert out.get("basis") == "consolidated"
    assert not any(u.endswith("/company/GRASIM/") for u in asked), \
        "fetched standalone even though consolidated was complete"
