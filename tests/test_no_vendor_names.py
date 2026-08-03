"""The site never tells a reader which data vendor a number came from.

That is a standing product rule, so it needs a guard that fails the build
rather than a habit of remembering. This checks the two surfaces a reader can
actually see:

    1. HTML text nodes -- page copy, headings, empty states.
    2. String literals inside <script> -- error messages, "source:" lines,
       anything assembled into innerHTML.

Explicitly NOT failures:

    * Code comments. Nobody renders those.
    * Property names like `d.upstox_ratios`. That is a JSON key in a file we
      bake ourselves, not text on a page.
    * The word "Screener" in "Fundamental Screener" -- that is the name of the
      scanX feature (a stock screener), a common noun that predates any vendor.
    * NSE and BSE. Those are the exchanges, not the vendor. A link to a BSE
      filing is the primary document, and the most useful thing on the page.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOCS = ROOT / "docs"

#: Tokens that identify a data vendor.
#:
#: Bare "Screener" IS caught. The original leak read "source: Screener
#: full-text-search" -- no ".in" anywhere -- so a rule keyed on the domain
#: would have sailed straight past the exact string it was written to stop.
#: The one allowed use is the app's own feature name, "Fundamental Screener",
#: carved out by the lookbehind.
VENDOR = re.compile(
    r"screener\.in"
    r"|(?<!fundamental )screener"
    r"|upstox|tradingview|yfinance|bseindia\.com",
    re.I,
)

PAGES = sorted(p for p in DOCS.glob("*.html"))


def _strip_comments_html(text: str) -> str:
    return re.sub(r"<!--.*?-->", " ", text, flags=re.S)


def _strip_comments_js(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    # Line comments: only when // is not inside a string or a URL (://).
    return re.sub(r"(?<![:\"'`\\])//[^\n]*", " ", text)


def _script_blocks(html: str) -> list[str]:
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)


def _text_nodes(html: str) -> str:
    """Everything outside <script> and <style> -- what a reader sees."""
    html = _strip_comments_html(html)
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


_STRING_LIT = re.compile(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"|`((?:[^`\\]|\\.)*)`")


def _string_literals(js: str) -> list[str]:
    js = _strip_comments_js(js)
    return [next(g for g in m.groups() if g is not None) for m in _STRING_LIT.finditer(js)]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_no_vendor_name_in_visible_page_copy(page):
    visible = _text_nodes(page.read_text(encoding="utf-8"))
    hits = [m.group() for m in VENDOR.finditer(visible)]
    assert not hits, f"{page.name} shows vendor name(s) {sorted(set(hits))} in page copy"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_no_vendor_name_in_rendered_strings(page):
    html = page.read_text(encoding="utf-8")
    bad = []
    for block in _script_blocks(html):
        for lit in _string_literals(block):
            if VENDOR.search(lit):
                bad.append(lit[:80])
    assert not bad, f"{page.name} builds vendor name(s) into output: {bad}"


def test_the_guard_would_actually_catch_a_regression():
    """A guard nobody has seen fail is not a guard."""
    assert VENDOR.search("Open TCS on Screener.in")
    assert VENDOR.search("filled from Upstox")
    literals = _string_literals("f('src').textContent='source: Screener full-text';")
    assert any(VENDOR.search(x) for x in literals)
    # ...and does not fire on the things we deliberately allow.
    # The bare-name form is the one that actually shipped. It must be caught.
    assert VENDOR.search("source: Screener full-text-search")
    # ...and these must not fire.
    assert not VENDOR.search("Fundamental Screener")
    assert not VENDOR.search("NSE/BSE delayed 15 min")


# --------------------------------------------- the escape trap, twice burned

def test_no_regex_in_the_repo_contains_a_literal_backspace():
    """`\b` written through a non-raw string becomes 0x08, and the pattern
    then matches NOTHING. It shipped that way twice here: once in the quota
    regex in refresh_debate.py, once in the vendor rules in redact.py. It is
    invisible in an editor and in a file listing, so only a byte-level check
    finds it."""
    offenders = []
    for path in list((ROOT / "earnings_intel").rglob("*.py")) + \
            list((ROOT / "scripts").rglob("*.py")):
        if b"\x08" in path.read_bytes():
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"literal backspace (0x08) in: {offenders}"


def test_the_redaction_rules_actually_fire():
    """A rule that matches nothing is worse than no rule: it reads as coverage."""
    import sys as _sys
    _sys.path.insert(0, str(ROOT))
    from earnings_intel.data.redact import redact

    cases = {
        "screener_pro": "flagged_pro",
        "screener_con": "flagged_con",
        "screener_note": "flagged_note",
        "Screener pro: Company is almost debt free.":
            "Flagged as a positive: Company is almost debt free.",
        "Screener con: high debtors of 162 days.":
            "Flagged as a negative: high debtors of 162 days.",
    }
    for raw, want in cases.items():
        assert redact(raw) == want, f"{raw!r} -> {redact(raw)!r}, wanted {want!r}"
