"""SWOT lands in the baked bundle — scripts/refresh_fundamentals.py.

The engine itself is covered by tests/test_swot.py; this file guards the BAKE:
that every bundle gains a "swot" key, that the sector row and the grounded
filing facts actually reach build_swot in the shape it reads, that a SWOT
failure never costs a company its bundle, and that the --ratios-only backfill
puts a SWOT on the ~5,400 bundles that were written before the engine existed —
including the token-less case, where no Upstox ratio is available at all.

No network, no API key: the Upstox path is monkeypatched everywhere.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import refresh_fundamentals as rf            # noqa: E402
from earnings_intel.data import swot as W    # noqa: E402


# --------------------------------------------------------------- fixtures
def _fund(code="AAA", name="Alpha Ltd"):
    """A fundamental block rich enough that several SWOT rules fire."""
    return {
        "code": code, "name": name,
        "url": "https://www.screener.in/company/AAA/",
        "overview": {"Market Cap": "Rs 12,000 Cr.", "Current Price": "Rs 540",
                     "Stock P/E": "18.4", "Book Value": "Rs 120",
                     "ROCE": "24.5 %", "ROE": "21.0 %", "Dividend Yield": "1.20 %"},
        "growth": {"Compounded Sales Growth": {"10 Years": "16%", "5 Years": "18%",
                                               "3 Years": "20%", "TTM": "22%"},
                   "Compounded Profit Growth": {"10 Years": "19%", "5 Years": "21%",
                                                "3 Years": "24%", "TTM": "26%"},
                   "Return on Equity": {"10 Years": "20%", "5 Years": "21%",
                                        "3 Years": "22%"}},
        "quarters": {"headers": ["Dec 2025", "Mar 2026"],
                     "rows": {"Sales": ["100", "120"], "Net Profit": ["10", "14"]}},
        "profit_loss": {"headers": ["Mar 2025", "Mar 2026"],
                        "rows": {"Sales": ["400", "480"], "Net Profit": ["40", "56"]}},
        "pros": ["Company has reduced debt."],
        "cons": ["Promoter holding has decreased over last 3 years."],
    }


def _bundle(code="AAA"):
    return {"generated_at": "2026-07-29", "fundamental": _fund(code),
            "prices": {"ok": True, "risk": {}, "technical": {}},
            "signal": {"label": "BUY", "composite": 68, "confidence": "medium",
                       "blocks": {}, "reasons_pos": [], "reasons_neg": []}}


def _docfile(code="AAA"):
    """docs/data/docs/<CODE>.json as refresh_docinsights.py writes it."""
    fact = {"claim": "Guided to 14-15% EBITDA margin for FY27",
            "quote": "We are guiding to a 14-15% EBITDA margin for FY27.",
            "doc_kind": "concall_transcript", "doc_date": "2026-05-02",
            "url": "https://example.com/call.pdf"}
    return {"code": code, "name": "Alpha Ltd", "generated_at": "2026-07-20",
            "documents": [{"kind": "concall_transcript"}],
            "analysis": {"summary": "Management guided up.",
                         "themes": {"guidance": [fact]},
                         "management_commitments": [],
                         "coverage": {"docs_analysed": 1}}}


@pytest.fixture
def docs_dir(tmp_path, monkeypatch):
    d = tmp_path / "docs"
    d.mkdir()
    monkeypatch.setattr(rf, "DOCS_DIR", d)
    return d


@pytest.fixture(autouse=True)
def no_sector(monkeypatch):
    """Never read the repo's real sector_tailwind.json from a test."""
    monkeypatch.setattr(rf.sl, "sector_for", lambda code=None, name=None, **kw: None)


# ------------------------------------------------------- _attach_swot basics
def test_attach_swot_adds_all_four_quadrants(docs_dir):
    b = _bundle()
    assert rf._attach_swot(b, "AAA") is True
    sw = b["swot"]
    for q in ("strengths", "weaknesses", "opportunities", "threats"):
        assert isinstance(sw[q], list)
    assert sw["strengths"], "a 24% ROCE has to produce at least one strength"
    assert sw["verdict"] and isinstance(sw["verdict"], str)
    assert set(sw["score"]) == {"s", "w", "o", "t"}


def test_attach_swot_items_are_evidence_backed(docs_dir):
    b = _bundle()
    rf._attach_swot(b, "AAA")
    for q in ("strengths", "weaknesses", "opportunities", "threats"):
        for item in b["swot"][q]:
            assert item["point"] and item["evidence"] and item["metric"]
            assert 1 <= item["weight"] <= 3


def test_attach_swot_output_is_json_serialisable(docs_dir):
    b = _bundle()
    rf._attach_swot(b, "AAA")
    assert json.loads(json.dumps(b, separators=(",", ":")))["swot"]["score"]["s"] >= 0


def test_swot_failure_never_breaks_a_company(docs_dir, monkeypatch, capsys):
    def boom(*a, **kw):
        raise RuntimeError("engine exploded")
    monkeypatch.setattr(rf.sw, "build_swot", boom)
    b = _bundle()
    assert rf._attach_swot(b, "AAA") is False
    assert "swot" not in b                       # no half-written block
    assert b["fundamental"]["code"] == "AAA"     # bundle otherwise untouched
    assert "swot skipped for AAA" in capsys.readouterr().out


def test_swot_failure_clears_a_stale_block(docs_dir, monkeypatch):
    monkeypatch.setattr(rf.sw, "build_swot",
                        lambda *a, **kw: (_ for _ in ()).throw(ValueError("nope")))
    b = _bundle()
    b["swot"] = {"strengths": [{"point": "stale"}]}
    assert rf._attach_swot(b, "AAA") is False
    assert "swot" not in b, "a failed recompute must not leave yesterday's SWOT behind"


def test_attach_swot_survives_a_garbage_bundle(docs_dir):
    b = {"fundamental": "not a dict"}
    assert rf._attach_swot(b, "AAA") is True
    assert b["swot"]["strengths"] == []


# ------------------------------------------------------------ sector wiring
def test_sector_row_reaches_build_swot(docs_dir, monkeypatch):
    seen = {}
    monkeypatch.setattr(rf.sl, "sector_for",
                        lambda code=None, name=None, **kw: {"name": "Chemicals",
                                                            "label": "TAILWIND",
                                                            "score": 0.9})
    real = rf.sw.build_swot
    monkeypatch.setattr(rf.sw, "build_swot",
                        lambda bundle, **kw: seen.update(kw) or real(bundle, **kw))
    b = _bundle()
    rf._attach_swot(b, "AAA")
    assert seen["sector"] == {"name": "Chemicals", "label": "TAILWIND", "score": 0.9}
    blob = json.dumps(b["swot"])
    assert "TAILWIND" in blob and "Chemicals" in blob


def test_sector_lookup_uses_the_company_name_too(docs_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(rf.sl, "sector_for",
                        lambda code=None, name=None, **kw: calls.append((code, name)))
    rf._attach_swot(_bundle(), "AAA")
    assert calls == [("AAA", "Alpha Ltd")]


def test_explicit_sector_row_skips_the_lookup(docs_dir, monkeypatch):
    monkeypatch.setattr(rf.sl, "sector_for",
                        lambda *a, **kw: pytest.fail("should not be called"))
    b = _bundle()
    rf._attach_swot(b, "AAA", {"name": "Metals & Mining", "label": "HEADWIND", "score": -0.7})
    assert "HEADWIND" in json.dumps(b["swot"])


def test_missing_sector_is_simply_fewer_points(docs_dir):
    b = _bundle()
    rf._attach_swot(b, "AAA")
    assert "sector_tailwind" not in json.dumps(b["swot"])
    assert b["swot"]["strengths"], "the rest of the SWOT still builds"


# ----------------------------------------------------------- filings wiring
def test_filings_for_reads_the_doc_file(docs_dir):
    (docs_dir / "AAA.json").write_text(json.dumps(_docfile()), encoding="utf-8")
    got = rf._filings_for("AAA")
    assert got["analysis"]["themes"]["guidance"][0]["claim"].startswith("Guided to")


def test_filings_for_missing_file_is_empty(docs_dir):
    assert rf._filings_for("NOPE") == {}


def test_filings_for_unreadable_file_is_empty(docs_dir):
    (docs_dir / "AAA.json").write_text("{not json", encoding="utf-8")
    assert rf._filings_for("AAA") == {}


def test_filings_for_non_dict_payload_is_empty(docs_dir):
    (docs_dir / "AAA.json").write_text("[1,2,3]", encoding="utf-8")
    assert rf._filings_for("AAA") == {}


def test_filings_for_accepts_a_bare_analysis_block(docs_dir):
    """swot reads filings["analysis"], so an unwrapped analysis gets re-wrapped."""
    bare = _docfile()["analysis"]
    (docs_dir / "AAA.json").write_text(json.dumps(bare), encoding="utf-8")
    got = rf._filings_for("AAA")
    assert got["analysis"]["themes"]["guidance"]


def test_filing_facts_reach_the_swot_verbatim(docs_dir):
    (docs_dir / "AAA.json").write_text(json.dumps(_docfile()), encoding="utf-8")
    b = _bundle()
    rf._attach_swot(b, "AAA")
    blob = json.dumps(b["swot"])
    assert "14-15% EBITDA margin" in blob, "the quoted sentence must survive into evidence"


def test_no_filing_file_means_no_filing_points(docs_dir):
    b = _bundle()
    rf._attach_swot(b, "AAA")
    for q in ("strengths", "weaknesses", "opportunities", "threats"):
        assert not [p for p in b["swot"][q] if str(p["metric"]).startswith("filing_")]


# ---------------------------------------------------------- ratios backfill
@pytest.fixture
def out_dir(tmp_path):
    d = tmp_path / "fundamental"
    d.mkdir()
    return d


def _write(out_dir, code, bundle):
    (out_dir / f"{code}.json").write_text(json.dumps(bundle), encoding="utf-8")


def _read(out_dir, code):
    return json.loads((out_dir / f"{code}.json").read_text(encoding="utf-8"))


def test_ratios_version_was_bumped_past_the_swot_less_bundles():
    assert rf.RATIOS_VERSION > 2, (
        "a stale RATIOS_VERSION makes the backfill skip today's bundles as fresh, "
        "so they would never gain a SWOT")


def test_backfill_adds_swot_without_any_upstox_token(docs_dir, out_dir, monkeypatch, capsys):
    """The token-less case: no ratios at all, yet the SWOT still has to land."""
    monkeypatch.setattr(rf, "_with_upstox_health", lambda fund, code: {})
    _write(out_dir, "AAA", _bundle())
    rf._backfill_ratios(out_dir, ["AAA"], "2026-07-29")
    saved = _read(out_dir, "AAA")
    assert saved["swot"]["strengths"]
    assert "upstox_ratios" not in saved
    assert "no ratios 1" in capsys.readouterr().out


def test_backfill_keeps_the_ratios_it_already_wrote(docs_dir, out_dir, monkeypatch):
    monkeypatch.setattr(rf, "_with_upstox_health",
                        lambda fund, code: {"current_ratio": {"value": 1.8}})
    _write(out_dir, "AAA", _bundle())
    rf._backfill_ratios(out_dir, ["AAA"], "2026-07-29")
    saved = _read(out_dir, "AAA")
    assert saved["upstox_ratios"] == {"current_ratio": {"value": 1.8}}
    assert saved["ratios_at"] == "2026-07-29"
    assert saved["ratios_v"] == rf.RATIOS_VERSION
    assert saved["swot"]["score"]["s"] > 0


def test_backfill_does_not_rescrape_screener(docs_dir, out_dir, monkeypatch):
    monkeypatch.setattr(rf, "_with_upstox_health", lambda fund, code: {})
    monkeypatch.setattr(rf.co, "fundamentals",
                        lambda *a, **kw: pytest.fail("backfill must not hit Screener"))
    _write(out_dir, "AAA", _bundle())
    rf._backfill_ratios(out_dir, ["AAA"], "2026-07-29")
    assert _read(out_dir, "AAA")["fundamental"]["name"] == "Alpha Ltd"


def test_backfill_preserves_the_rest_of_the_bundle(docs_dir, out_dir, monkeypatch):
    monkeypatch.setattr(rf, "_with_upstox_health", lambda fund, code: {})
    b = _bundle()
    b["prices"]["yearwise"] = [{"year": 2025, "ret": 12.5}]
    _write(out_dir, "AAA", b)
    rf._backfill_ratios(out_dir, ["AAA"], "2026-07-29")
    saved = _read(out_dir, "AAA")
    assert saved["generated_at"] == "2026-07-29"
    assert saved["prices"]["yearwise"] == [{"year": 2025, "ret": 12.5}]
    assert saved["signal"]["label"] == "BUY"


def test_backfill_skips_a_bundle_that_is_already_fresh(docs_dir, out_dir, monkeypatch, capsys):
    monkeypatch.setattr(rf, "_with_upstox_health",
                        lambda fund, code: pytest.fail("a fresh bundle must not be recomputed"))
    b = _bundle()
    b.update({"upstox_ratios": {"current_ratio": {"value": 2.0}},
              "ratios_at": "2026-07-29", "ratios_v": rf.RATIOS_VERSION,
              "swot": {"strengths": [], "weaknesses": [], "opportunities": [],
                       "threats": [], "score": {"s": 0, "w": 0, "o": 0, "t": 0}}})
    _write(out_dir, "AAA", b)
    rf._backfill_ratios(out_dir, ["AAA"], "2026-07-29")
    assert "already-fresh 1" in capsys.readouterr().out


def test_fresh_ratios_without_a_swot_are_not_skipped(docs_dir, out_dir, monkeypatch):
    """The whole point of the backfill: a swot-less bundle must be revisited."""
    monkeypatch.setattr(rf, "_with_upstox_health",
                        lambda fund, code: {"current_ratio": {"value": 2.0}})
    b = _bundle()
    b.update({"upstox_ratios": {"current_ratio": {"value": 2.0}},
              "ratios_at": "2026-07-29", "ratios_v": rf.RATIOS_VERSION})
    _write(out_dir, "AAA", b)
    rf._backfill_ratios(out_dir, ["AAA"], "2026-07-29")
    assert _read(out_dir, "AAA")["swot"]["strengths"]


def test_backfill_leaves_a_bundle_alone_when_nothing_can_be_computed(
        docs_dir, out_dir, monkeypatch):
    monkeypatch.setattr(rf, "_with_upstox_health", lambda fund, code: {})
    monkeypatch.setattr(rf, "_attach_swot", lambda *a, **kw: False)
    before = _bundle()
    _write(out_dir, "AAA", before)
    rf._backfill_ratios(out_dir, ["AAA"], "2026-07-29")
    assert _read(out_dir, "AAA") == before


def test_backfill_ignores_codes_with_no_bundle_on_disk(docs_dir, out_dir, monkeypatch):
    monkeypatch.setattr(rf, "_with_upstox_health", lambda fund, code: {})
    rf._backfill_ratios(out_dir, ["GHOST"], "2026-07-29")
    assert list(out_dir.glob("*.json")) == []


def test_backfill_survives_a_corrupt_bundle(docs_dir, out_dir, monkeypatch, capsys):
    monkeypatch.setattr(rf, "_with_upstox_health", lambda fund, code: {})
    (out_dir / "BAD.json").write_text("{truncated", encoding="utf-8")
    _write(out_dir, "AAA", _bundle())
    assert rf._backfill_ratios(out_dir, ["BAD", "AAA"], "2026-07-29") == 0
    assert _read(out_dir, "AAA")["swot"]["strengths"]
    assert "failed 1" in capsys.readouterr().out


def test_backfill_writes_atomically(docs_dir, out_dir, monkeypatch):
    monkeypatch.setattr(rf, "_with_upstox_health", lambda fund, code: {})
    _write(out_dir, "AAA", _bundle())
    rf._backfill_ratios(out_dir, ["AAA"], "2026-07-29")
    assert list(out_dir.glob("*.tmp")) == [], "no temp file may be left behind"


def test_backfill_passes_the_sector_row_through(docs_dir, out_dir, monkeypatch):
    monkeypatch.setattr(rf, "_with_upstox_health", lambda fund, code: {})
    monkeypatch.setattr(rf.sl, "sector_for",
                        lambda code=None, name=None, **kw: {"name": "Healthcare",
                                                            "label": "HEADWIND",
                                                            "score": -0.5})
    _write(out_dir, "AAA", _bundle())
    rf._backfill_ratios(out_dir, ["AAA"], "2026-07-29")
    assert "HEADWIND" in json.dumps(_read(out_dir, "AAA")["swot"])


def test_backfill_passes_the_filings_through(docs_dir, out_dir, monkeypatch):
    monkeypatch.setattr(rf, "_with_upstox_health", lambda fund, code: {})
    (docs_dir / "AAA.json").write_text(json.dumps(_docfile()), encoding="utf-8")
    _write(out_dir, "AAA", _bundle())
    rf._backfill_ratios(out_dir, ["AAA"], "2026-07-29")
    assert "14-15% EBITDA margin" in json.dumps(_read(out_dir, "AAA")["swot"])


def test_backfill_sees_the_ratios_it_just_merged(docs_dir, out_dir, monkeypatch):
    """SWOT runs AFTER the health merge, so this pass's peers are visible to it."""
    order = []

    def health(fund, code):
        order.append("health")
        fund.setdefault("analysis", {})["health"] = {
            "peers": {"roce": {"value": 26.0, "sector": 12.0, "unit": "pct",
                               "bias": "positive"}}}
        return {"roce": {"value": 26.0}}

    monkeypatch.setattr(rf, "_with_upstox_health", health)
    real = rf.sw.build_swot
    monkeypatch.setattr(rf.sw, "build_swot",
                        lambda b, **kw: order.append("swot") or real(b, **kw))
    _write(out_dir, "AAA", _bundle())
    rf._backfill_ratios(out_dir, ["AAA"], "2026-07-29")
    assert order == ["health", "swot"]
    assert "sector median" in json.dumps(_read(out_dir, "AAA")["swot"])


def test_backfill_honours_the_time_budget(docs_dir, out_dir, monkeypatch):
    monkeypatch.setattr(rf, "_with_upstox_health", lambda fund, code: {})
    clock = iter([0.0] + [10_000.0] * 20)
    monkeypatch.setattr(rf.time, "time", lambda: next(clock))
    _write(out_dir, "AAA", _bundle())
    rf._backfill_ratios(out_dir, ["AAA"], "2026-07-29", max_minutes=1)
    assert "swot" not in _read(out_dir, "AAA"), "the budget must stop the pass"


# ------------------------------------------------------ engine parity check
def test_bake_and_engine_agree(docs_dir):
    """What the bake stores is exactly what build_swot returns for that bundle."""
    b = _bundle()
    rf._attach_swot(b, "AAA")
    direct = W.build_swot({k: v for k, v in b.items() if k != "swot"},
                          sector=None, filings=None)
    assert b["swot"] == direct
