"""Document-analysis engine: grounding enforcement, merge/dedupe, prompt, pipeline.

All network and LLM calls are injected/monkeypatched — no key, no requests.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from earnings_intel.data import docanalysis as da   # noqa: E402

# --- a fake "document text" as pdfplumber would hand it over (hard newlines) ---
SRC = """Tata Motors Limited Q4 FY26 Earnings Call
We are guiding to an EBITDA margin of 12-14% for FY27 on the back of
better mix and cost programmes.
Demand in the domestic passenger vehicle market remained resilient through the quarter.
We will invest Rs 18,000 crore of capex in FY27 across products and capacity.
The order book stood at 45,000 units at the end of March 2026.
We remain committed to becoming net-debt free by FY27.
Commodity inflation and tariff changes remain the key headwinds we are watching."""

DOC = {"kind": "concall_transcript", "date": "2026-05-01",
       "title": "Q4 FY26 Earnings Call Transcript",
       "url": "https://example.com/q4fy26-transcript.pdf", "source": "screener"}

DOC_OLD = {"kind": "concall_ppt", "date": "2026-02-01", "title": "Q3 FY26 PPT",
           "url": "https://example.com/q3fy26.pdf", "source": "bse"}


def _fact(claim, quote, **kw):
    d = {"claim": claim, "quote": quote}
    d.update(kw)
    return d


MODEL_JSON = {
    "summary": "Management guided to a 12-14% EBITDA margin for FY27. Capex of "
               "Rs 18,000 crore is planned for FY27.",
    "themes": {
        "guidance": [
            _fact("Management guided to 12-14% EBITDA margin for FY27",
                  "We are guiding to an EBITDA margin of 12-14% for FY27"),
            _fact("Management expects revenue to double by FY30",          # not in SRC
                  "We expect revenue to double by FY30 across all segments"),
        ],
        "demand_outlook": [
            _fact("Domestic PV demand stayed resilient in the quarter",
                  "Demand in the domestic passenger vehicle market remained resilient through the quarter."),
        ],
        "capex_expansion": [
            _fact("FY27 capex of Rs 18,000 crore",
                  "We will invest Rs 18,000 crore of capex in FY27 across products and capacity."),
        ],
        "orders_capacity": [
            _fact("Order book of 45,000 units at March 2026",
                  "The order book stood at 45,000 units at the end of March 2026."),
        ],
        "risks": [                                                          # alias key
            _fact("Commodity inflation and tariffs flagged as headwinds",
                  "Commodity inflation and tariff changes remain the key headwinds we are watching."),
        ],
        "margins_costs": [
            {"claim": "Margins improving", "quote": ""},                    # no quote -> dropped
        ],
    },
    "management_commitments": [
        _fact("Management committed to net-debt-free status by FY27",
              "We remain committed to becoming net-debt free by FY27.", timeframe="FY27"),
        _fact("Management committed to a 20% dividend payout",              # not in SRC
              "We commit to a dividend payout ratio of 20% every year", timeframe="FY27"),
    ],
}


# ----------------------------------------------------------------- normalise
def test_normalise_collapses_whitespace_and_typography():
    assert da.normalise("a\n b\t\tc  ") == "a b c"
    assert da.normalise("we said “yes” — now") == 'we said "yes" - now'
    assert da.normalise("") == "" and da.normalise(None) == ""


# ---------------------------------------------------------------- is_grounded
def test_is_grounded_exact_match():
    assert da.is_grounded("The order book stood at 45,000 units", SRC) is True


def test_is_grounded_survives_newlines_and_spacing():
    # the source wraps this sentence across a hard newline; the quote does not
    q = "We are guiding to an EBITDA margin of 12-14% for FY27 on the back of better mix"
    assert "\n" in SRC and q not in SRC          # verbatim only AFTER normalisation
    assert da.is_grounded(q, SRC) is True
    assert da.is_grounded("We  are\nguiding   to an EBITDA margin of 12-14%", SRC) is True


def test_is_grounded_rejects_absent_short_and_sourceless():
    assert da.is_grounded("We expect revenue to double by FY30", SRC) is False
    assert da.is_grounded("margin", SRC) is False          # too short to prove anything
    assert da.is_grounded("", SRC) is False
    assert da.is_grounded("The order book stood at 45,000 units", "") is False


# ---------------------------------------------------------- enforce_grounding
def test_enforce_grounding_keeps_grounded_drops_ungrounded():
    clean, dropped = da.enforce_grounding(MODEL_JSON, DOC, SRC)
    guidance = clean["themes"]["guidance"]
    assert len(guidance) == 1
    assert guidance[0]["claim"] == "Management guided to 12-14% EBITDA margin for FY27"
    assert clean["themes"]["demand_outlook"] and clean["themes"]["capex_expansion"]
    assert clean["themes"]["orders_capacity"]
    assert clean["themes"]["risks_headwinds"]              # alias key folded in
    assert clean["themes"]["margins_costs"] == []          # quote-less fact dropped
    assert clean["themes"]["capital_allocation"] == []     # nothing stated -> empty
    # 1 hallucinated guidance + 1 quote-less margin + 1 hallucinated commitment
    assert dropped == 3


def test_enforce_grounding_counts_and_stamps_provenance():
    clean, dropped = da.enforce_grounding(MODEL_JSON, DOC, SRC)
    kept = sum(len(v) for v in clean["themes"].values()) + len(clean["management_commitments"])
    assert kept == 6 and dropped == 3          # 9 facts offered, 3 unprovable
    for f in clean["themes"]["guidance"] + clean["management_commitments"]:
        assert f["doc_kind"] == DOC["kind"]
        assert f["doc_date"] == DOC["date"]
        assert f["url"] == DOC["url"]
        assert set(f) >= {"claim", "quote", "doc_kind", "doc_date", "url"}
    c = clean["management_commitments"][0]
    assert c["timeframe"] == "FY27"                        # commitments carry a timeframe
    assert "timeframe" not in clean["themes"]["guidance"][0]


def test_enforce_grounding_ignores_model_supplied_provenance():
    bad = {"themes": {"guidance": [_fact(
        "Order book of 45,000 units",
        "The order book stood at 45,000 units at the end of March 2026.",
        url="https://evil.example/inject", doc_date="1999-01-01")]}}
    clean, dropped = da.enforce_grounding(bad, DOC, SRC)
    f = clean["themes"]["guidance"][0]
    assert f["url"] == DOC["url"] and f["doc_date"] == DOC["date"] and dropped == 0


def test_enforce_grounding_empty_and_garbage_inputs():
    clean, dropped = da.enforce_grounding({}, DOC, SRC)
    assert dropped == 0 and clean["summary"] == ""
    assert list(clean["themes"]) == list(da.THEMES)
    assert all(v == [] for v in clean["themes"].values())
    clean, dropped = da.enforce_grounding({"themes": {"guidance": "nope"}}, DOC, SRC)
    assert clean["themes"]["guidance"] == [] and dropped == 0
    # every fact is ungrounded when there is no source text at all
    clean, dropped = da.enforce_grounding(MODEL_JSON, DOC, "")
    assert dropped == 9 and clean["management_commitments"] == []
    assert all(v == [] for v in clean["themes"].values())


# --------------------------------------------------------------- merge/dedupe
def test_merge_facts_newest_first_and_dedupes():
    old = [{"claim": "EBITDA margin guidance of 12-14% for FY27", "quote": "q1",
            "doc_date": "2026-02-01", "url": "u1"}]
    new = [{"claim": "EBITDA margin guidance of 12-14% for FY27!", "quote": "q2",
            "doc_date": "2026-05-01", "url": "u2"},
           {"claim": "Order book at 45,000 units", "quote": "q3",
            "doc_date": "2026-05-01", "url": "u3"}]
    out = da.merge_facts([old, new])
    assert [f["doc_date"] for f in out] == ["2026-05-01", "2026-05-01"]
    assert out[0]["url"] == "u2"                  # newest copy of the duplicate claim wins
    assert len(out) == 2                          # near-identical older claim deduped


def test_dedupe_facts_on_identical_quote_and_blank_rows():
    facts = [{"claim": "A different wording entirely", "quote": "same quote text here"},
             {"claim": "Yet another phrasing of it", "quote": "Same  quote   text here"},
             {"claim": "", "quote": "x"}, "not-a-dict"]
    assert len(da.dedupe_facts(facts)) == 1
    assert da.merge_facts([]) == [] and da.merge_facts([[], None]) == []


def test_merge_summaries_dedupes_and_caps():
    s = da.merge_summaries(["One. Two. Three.", "Three. Four. Five. Six."],
                           max_sentences=5)
    assert s == "One. Two. Three. Four. Five."
    assert da.merge_summaries([]) == ""


# ------------------------------------------------------------- doc selection
def test_select_documents_recency_kind_preference_and_limit():
    docs = [DOC_OLD, DOC,
            {"kind": "concall_ppt", "date": "2026-05-01", "title": "Q4 PPT",
             "url": "https://x/ppt.pdf"},
            {"kind": "announcement", "date": "2026-06-01", "title": "Board meeting",
             "url": "https://x/ann.pdf"},
            {"kind": "annual_report", "date": "2026-05-01", "title": "AR",
             "url": "https://x/ar.pdf"},
            {"kind": "concall_notes", "date": "2026-05-01", "title": "no url", "url": ""}]
    sel = da.select_documents(docs)
    assert [d["kind"] for d in sel] == ["concall_transcript", "concall_ppt",
                                        "annual_report", "concall_ppt"]
    assert all(d["kind"] != "announcement" for d in sel)     # announcements never deep-read
    assert len(da.select_documents(docs, limit=2)) == 2
    assert da.select_documents([]) == [] and da.select_documents(None) == []


def test_announcement_headlines():
    docs = [DOC, {"kind": "announcement", "date": "2026-06-01", "title": "Order win",
                  "url": "u"},
            {"kind": "announcement", "date": "2026-06-10", "title": "Board meeting",
             "url": "u"}]
    h = da.announcement_headlines(docs)
    assert h[0].startswith("2026-06-10") and "Board meeting" in h[0]
    assert len(h) == 2


# ------------------------------------------------------------- prompt / parse
def test_build_prompt_has_rules_themes_and_text():
    p = da.build_prompt("Tata Motors Ltd", DOC, SRC, ["2026-06-01 — Order win"])
    for needle in ("Tata Motors Ltd", "concall_transcript", "VERBATIM",
                   "no buy/sell/hold recommendation", "no price targets",
                   "management_commitments", "timeframe", "DOCUMENT TEXT",
                   "order book stood at 45,000 units", "Order win"):
        assert needle in p, needle
    for t in da.THEMES:
        assert t in p, t
    assert "RECENT EXCHANGE ANNOUNCEMENTS" not in da.build_prompt("X", DOC, SRC)


def test_extract_obj_handles_fences_and_garbage():
    assert da._extract_obj('```json\n{"summary":"s"}\n```')["summary"] == "s"
    assert da._extract_obj("prose {\"a\": 1} trailing")["a"] == 1
    assert da._extract_obj("not json") == {}
    assert da._extract_obj("") == {} and da._extract_obj("[1,2]") == {}


# ------------------------------------------------------------------ pipeline
def _fake_fetch(url):
    return (SRC, None) if url.endswith(".pdf") else (None, "unsupported")


def test_analyse_documents_end_to_end(monkeypatch):
    import json as _json
    seen = {}

    def fake_call(prompt, provider="gemini", key=None, model=None):
        seen.setdefault("prompts", []).append(prompt)
        return "```json\n" + _json.dumps(MODEL_JSON) + "\n```"

    monkeypatch.setattr(da, "_call_model", fake_call)
    monkeypatch.setattr(da.ia, "have_key", lambda: True)

    out = da.analyse_documents([DOC, DOC_OLD], "Tata Motors Ltd", fetch=_fake_fetch)
    assert "error" not in out
    assert list(out["themes"]) == list(da.THEMES)
    assert out["coverage"] == {"docs_analysed": 2,
                               "kinds": ["concall_transcript", "concall_ppt"]}
    assert len(seen["prompts"]) == 2
    # the same model output for both docs -> identical claims merged away
    assert len(out["themes"]["guidance"]) == 1
    assert out["themes"]["guidance"][0]["doc_date"] == "2026-05-01"   # newest kept
    assert out["themes"]["margins_costs"] == []
    assert out["management_commitments"][0]["timeframe"] == "FY27"
    m = out["_meta"]
    assert m["grounded"] is True
    assert m["facts_dropped_ungrounded"] == 6          # 3 per document
    assert m["facts_kept"] == 6 == sum(len(v) for v in out["themes"].values()) \
        + len(out["management_commitments"])
    assert out["summary"].startswith("Management guided to a 12-14% EBITDA margin")


def test_analyse_documents_no_key_degrades(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(da.ia, "have_key", lambda: False)
    out = da.analyse_documents([DOC], "Tata Motors Ltd", fetch=_fake_fetch)
    assert "key" in out["error"].lower()
    assert out["themes"] == {t: [] for t in da.THEMES}
    assert out["coverage"]["docs_analysed"] == 0
    assert out["_meta"]["facts_kept"] == 0 and out["summary"] == ""
    # provider hooks: openai/anthropic without a key degrade the same way
    for prov in ("openai", "anthropic"):
        o = da.analyse_documents([DOC], "X", fetch=_fake_fetch, provider=prov)
        assert "error" in o and o["_meta"]["model"] == prov


def test_analyse_documents_empty_and_announcement_only(monkeypatch):
    monkeypatch.setattr(da.ia, "have_key", lambda: True)
    for docs in ([], None, [{"kind": "announcement", "date": "2026-06-01",
                             "title": "Board meeting", "url": "https://x/a.pdf"}]):
        out = da.analyse_documents(docs, "X", fetch=_fake_fetch)
        assert "error" in out and "document" in out["error"]
        assert out["coverage"]["docs_analysed"] == 0
        assert list(out["themes"]) == list(da.THEMES)


def test_analyse_documents_survives_fetch_and_model_failures(monkeypatch):
    monkeypatch.setattr(da.ia, "have_key", lambda: True)
    monkeypatch.setattr(da, "_call_model",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    def dead_fetch(url):
        raise OSError("connection reset")

    out = da.analyse_documents([DOC], "X", fetch=dead_fetch)
    assert "error" in out and "OSError" in out["error"]
    assert out["coverage"]["docs_analysed"] == 0

    out = da.analyse_documents([DOC], "X", fetch=lambda u: (SRC, None))
    assert "error" in out and "boom" in out["error"]      # model failure, no raise
    assert out["_meta"]["facts_kept"] == 0


def test_analyse_documents_partial_failure_still_returns_facts(monkeypatch):
    import json as _json
    monkeypatch.setattr(da.ia, "have_key", lambda: True)
    monkeypatch.setattr(da, "_call_model",
                        lambda *a, **k: _json.dumps(MODEL_JSON))
    out = da.analyse_documents(
        [DOC, {"kind": "annual_report", "date": "2026-04-01", "title": "AR",
               "url": "https://x/ar.html"}],
        "X", fetch=_fake_fetch)
    assert "error" not in out                        # one doc worked
    assert out["coverage"] == {"docs_analysed": 1, "kinds": ["concall_transcript"]}
    assert "Skipped" in out["_meta"]["note"]
    assert out["themes"]["guidance"][0]["url"] == DOC["url"]


def test_fetch_may_return_bare_text(monkeypatch):
    import json as _json
    monkeypatch.setattr(da.ia, "have_key", lambda: True)
    monkeypatch.setattr(da, "_call_model", lambda *a, **k: _json.dumps(MODEL_JSON))
    out = da.analyse_documents([DOC], "X", fetch=lambda u: SRC)
    assert out["coverage"]["docs_analysed"] == 1 and out["_meta"]["facts_kept"] == 6


def test_default_fetch_rejects_empty_url():
    text, err = da._default_fetch("")
    assert text is None and err == "no url"
