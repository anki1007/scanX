"""The Gemini key is a SERVER-SIDE bake credential.

It analyses concalls/PPTs/reports in CI and must never reach a user: not in the
published JSON, not in a page, not via any runtime endpoint. These tests fail
loudly if a future change starts leaking it.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

SENTINEL = "AIzaSyTESTKEYSENTINELdoNOTleak0000000000"


def test_baked_bundle_never_carries_the_key(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", SENTINEL)
    import refresh_docinsights as rd

    analysis = {
        "summary": "Management guided to higher margins.",
        "themes": {"guidance": [{"claim": "c", "quote": "q", "doc_kind": "concall_transcript",
                                 "doc_date": "2026-05-01", "url": "https://x/y.pdf"}]},
        "management_commitments": [],
        "coverage": {"docs_analysed": 1, "kinds": ["concall_transcript"]},
    }
    bundle = rd.build_bundle("ACME", "Acme Ltd",
                             [{"kind": "concall_transcript", "date": "2026-05-01",
                               "title": "t", "url": "https://x/y.pdf", "source": "screener"}],
                             analysis, "2026-07-27")
    blob = json.dumps(bundle)
    assert SENTINEL not in blob
    assert not re.search(r"AIza[0-9A-Za-z_-]{20,}", blob)
    assert "GEMINI_API_KEY" not in blob


def test_no_published_doc_bundle_contains_a_key_pattern():
    """Any bundle already baked into docs/ must be clean."""
    out = ROOT / "docs" / "data" / "docs"
    for p in out.glob("*.json"):
        text = p.read_text(encoding="utf-8", errors="replace")
        assert not re.search(r"AIza[0-9A-Za-z_-]{20,}", text), p.name
        assert "GEMINI_API_KEY" not in text, p.name


def test_no_frontend_page_references_a_gemini_key_or_endpoint():
    """The site is static: pages read baked JSON, never an AI API."""
    for page in (ROOT / "docs").glob("*.html"):
        s = page.read_text(encoding="utf-8", errors="replace")
        assert "GEMINI_API_KEY" not in s, page.name
        assert not re.search(r"AIza[0-9A-Za-z_-]{20,}", s), page.name
        assert "generativelanguage.googleapis.com" not in s, page.name


def test_local_server_exposes_no_ai_endpoint():
    """serve.py must not proxy an LLM — that would hand the key to any caller."""
    s = (ROOT / "scripts" / "serve.py").read_text(encoding="utf-8", errors="replace")
    for forbidden in ("ai_chat", "insights_ai", "docanalysis",
                      "generativelanguage", "GEMINI_API_KEY"):
        assert forbidden not in s, forbidden
