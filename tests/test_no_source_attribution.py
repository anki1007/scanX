"""No data-provider name may be RENDERED on any page.

The pipeline still records where a number came from — `source` fields stay in
the baked JSON, because the grounding and audit work depends on knowing which
feed a value arrived on. This is purely about what reaches a reader's screen.

Deliberately narrow: a fetch URL, an href to a company page, or a code comment
naming a provider is not user-visible and is left alone. Only text that renders
counts.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DOCS = ROOT / "docs"

PROVIDERS = re.compile(r"screener\.?in|upstox|yahoo|yfinance|nsdl|vahan|parivahan", re.I)
# lines that cannot reach the screen
INVISIBLE = re.compile(r"href=|fetch\(|\.json|screener\.in/company|script src=|^\s*(//|\*|/\*)")


def _pages():
    return sorted(DOCS.glob("*.html")) + sorted((DOCS / "vendor").glob("*.js"))


def _visible_hits(text):
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)     # comments are not rendered
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if not PROVIDERS.search(line) or INVISIBLE.search(line):
            continue
        rendered_markup = re.search(r">[^<]*(?:screener|upstox|yahoo|nsdl|vahan|parivahan)",
                                    line, re.I)
        rendered_string = re.search(r"'[^']*(?:Screener|Upstox|Yahoo|NSDL|Vahan)[^']*'", line)
        if rendered_markup or rendered_string:
            out.append(f"line {i}: {line.strip()[:110]}")
    return out


def test_no_page_shows_a_data_provider_name():
    problems = []
    for page in _pages():
        for hit in _visible_hits(page.read_text(encoding="utf-8")):
            problems.append(f"{page.name} {hit}")
    assert not problems, "provider names visible to readers:\n  " + "\n  ".join(problems)


def test_the_debate_evidence_panel_does_not_print_its_source():
    """Every evidence item carries source: 'Screener.in' / 'Upstox key-ratios',
    and the panel used to render it per row."""
    html = (DOCS / "fundamental.html").read_text(encoding="utf-8")
    panel = html[html.index("devi"):html.index("devi") + 4000]
    assert "esc(e.source" not in panel, "the evidence panel still prints e.source"


def test_the_json_still_records_the_source_internally():
    """Hiding it from the page must NOT strip it from the data — the grounding
    check, the peer-ratio audit and the source_conflict edges all depend on
    knowing which feed a number arrived on."""
    import json
    idx = json.loads((DOCS / "data" / "debate" / "index.json").read_text(encoding="utf-8"))
    assert idx["codes"], "no debates baked to check"
    code = idx["codes"][0]["code"]
    bundle = json.loads((DOCS / "data" / "debate" / f"{code}.json").read_text(encoding="utf-8"))
    assert any(e.get("source") for e in bundle["evidence"]), \
        "source was removed from the data, not just the display"
