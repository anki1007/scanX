"""Debate bake (scripts/refresh_debate.py) — pure functions, no network, no API key.

This is the only LLM-PRICED board, so the tests guard the things that cost money
or lie: which companies get picked, whether "already debated today" is trusted
from the stamp INSIDE the file (mtime breaks in CI), that the evidence handed to
the model is actually assembled from the baked artefacts, that an empty debate
never overwrites a good one, and that a total model outage exits NON-ZERO instead
of reading like "nobody had anything to say".

earnings_intel/data/debate.py is written by a different pass and may not exist
yet — every test injects a fake module, so nothing here imports it for real.
"""
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import refresh_debate as rd            # noqa: E402


def baked(out):
    """Debate files written, excluding the coverage index the bake also publishes."""
    return sorted(p.stem for p in Path(out).glob("*.json") if p.stem != "index")


BOARD = [
    {"code": "AAA", "name": "Alpha Ltd", "composite": 51, "sector": "Chemicals"},
    {"code": "BBB", "name": "Beta Ltd", "composite": 78, "sector": "Healthcare"},
    {"code": "CCC", "name": "Gamma Ltd", "composite": 65, "sector": "Metals & Mining"},
    {"code": "", "name": "no code", "composite": 99},
]

TAILWIND = {"full_market": {"score": 0.6},
            "sectors": [
                {"sector": "Metals & Mining", "signal": "TAILWIND", "score": 1.066, "n": 99},
                {"sector": "Healthcare", "signal": "HEADWIND", "score": -0.4, "n": 331},
            ]}


def _fund(code="AAA", name="Alpha Ltd"):
    """A docs/data/fundamental/<CODE>.json as refresh_fundamentals writes it."""
    return {"generated_at": "2026-07-28",
            "fundamental": {"code": code, "name": name,
                            "overview": {"Stock P/E": "48.6"},
                            "quarters": {"Sales": [1, 2]},
                            "pros": ["reduced debt"], "cons": ["rich valuation"],
                            "analysis": {"dcf": {"verdict": "overvalued"}}},
            "prices": {"yearwise": [], "risk": {}, "technical": {}},
            "signal": {"label": "BUY", "composite": 65, "reasons_pos": ["growth"]},
            "upstox_ratios": {"current_ratio": 1.4}}


def _docs(code="AAA"):
    """A docs/data/docs/<CODE>.json as refresh_docinsights writes it."""
    fact = {"claim": "Guided to 12-14% margin", "quote": "We guide to 12-14% margin.",
            "doc_kind": "concall_transcript", "doc_date": "2026-05-01",
            "url": "https://ex.com/t.pdf"}
    return {"code": code, "name": "Alpha Ltd", "generated_at": "2026-07-27",
            "documents": [{"kind": "concall_transcript", "url": "https://ex.com/t.pdf"}],
            "analysis": {"summary": "Management said things.",
                         "themes": {"guidance": [fact]},
                         "management_commitments": [dict(fact, timeframe="FY27")],
                         "coverage": {"docs_analysed": 2}},
            "_meta": {"model": "gemini-2.5-flash"}}


def _result(rounds=2, verdict="bull edges it"):
    """What debate.run_debate is expected to hand back."""
    return {"rounds": [{"bull": f"bull {i}", "bear": f"bear {i}"} for i in range(rounds)],
            "verdict": verdict,
            "_meta": {"model": "gemini-2.5-flash", "provider": "gemini", "note": ""}}


# ------------------------------------------------------------------ universe
def test_universe_sorts_by_composite_and_caps_at_top():
    codes, rows = rd.universe(BOARD, top=2)
    assert codes == ["BBB", "CCC"]                    # 78, 65 — not board order
    assert rows["BBB"]["name"] == "Beta Ltd"


def test_universe_appends_extra_codes_after_the_board():
    codes, _ = rd.universe(BOARD, top=1, codes="TATAMOTORS, lt")
    assert codes == ["BBB", "TATAMOTORS", "LT"]


def test_universe_dedupes_codes_already_in_the_board():
    codes, _ = rd.universe(BOARD, top=3, codes="CCC,AAA")
    assert codes == ["BBB", "CCC", "AAA"]


def test_universe_drops_blank_codes_and_non_dict_rows():
    codes, _ = rd.universe(BOARD + ["junk", None, {"name": "x"}], top=10)
    assert codes == ["BBB", "CCC", "AAA"]


def test_universe_top_zero_is_codes_only():
    codes, _ = rd.universe(BOARD, top=0, codes="LT")
    assert codes == ["LT"]


def test_universe_default_top_is_the_cheap_100():
    """The priced board must not default to the whole market."""
    import inspect
    assert inspect.signature(rd.universe).parameters["top"].default == 100


def test_universe_empty_board():
    assert rd.universe([], top=100) == ([], {})
    assert rd.universe(None, top=100, codes="LT")[0] == ["LT"]


def test_universe_keeps_the_best_row_for_a_duplicated_code():
    dupes = [{"code": "AAA", "composite": 10, "sector": "Old"},
             {"code": "AAA", "composite": 90, "sector": "New"}]
    codes, rows = rd.universe(dupes, top=5)
    assert codes == ["AAA"] and rows["AAA"]["sector"] == "New"


def test_board_field_reads_name_and_sector():
    _, rows = rd.universe(BOARD, top=3)
    assert rd.board_field(rows, "BBB", "sector") == "Healthcare"
    assert rd.board_field(rows, "NOPE", "sector") == ""
    assert rd.board_field({}, "BBB", "name") == ""


def test_split_codes_normalises_and_dedupes():
    assert rd.split_codes(" tatamotors ,LT,,lt; RELIANCE ") == ["TATAMOTORS", "LT", "RELIANCE"]
    assert rd.split_codes("") == [] and rd.split_codes(None) == []


def test_read_board_recovers_truncated_write(tmp_path):
    p = tmp_path / "technofunda.json"
    good = json.dumps(BOARD[:3])
    p.write_bytes(good[:-14].encode() + b"\x00" * 8)      # truncated mid-object + null pad
    assert [r["code"] for r in rd._read_board(p)] == ["AAA", "BBB"]
    assert rd._read_board(tmp_path / "missing.json") == []


# -------------------------------------------------- generated_at skip logic
def test_baked_on_reads_stamp_from_inside_the_file(tmp_path):
    p = tmp_path / "AAA.json"
    rd._atomic(p, json.dumps(rd.build_bundle("AAA", "Alpha", _result(), today="2026-07-29"),
                             separators=(",", ":")))
    assert rd._baked_on(p) == "2026-07-29"


def test_baked_on_is_blank_for_legacy_and_unreadable_files(tmp_path):
    legacy = tmp_path / "L.json"
    legacy.write_text('{"code":"L","debate":{}}', encoding="utf-8")
    assert rd._baked_on(legacy) == ""
    assert rd._baked_on(tmp_path / "nope.json") == ""


def test_baked_on_ignores_mtime_not_content(tmp_path):
    """A fresh CI checkout stamps every file 'today' — the date must come from the JSON."""
    import os
    import time as _t
    p = tmp_path / "AAA.json"
    rd._atomic(p, json.dumps(rd.build_bundle("AAA", "A", _result(), today="2020-01-02"),
                             separators=(",", ":")))
    os.utime(p, (_t.time(), _t.time()))                   # "checked out just now"
    assert rd._baked_on(p) == "2020-01-02"
    assert rd._skip_baked(p, "2026-07-29") is False       # stale -> re-debate


def test_skip_baked_only_when_stamp_matches_today(tmp_path):
    p = tmp_path / "AAA.json"
    rd._atomic(p, json.dumps(rd.build_bundle("AAA", "A", _result(), today="2026-07-29"),
                             separators=(",", ":")))
    assert rd._skip_baked(p, "2026-07-29") is True
    assert rd._skip_baked(p, "2026-07-30") is False
    assert rd._skip_baked(p, "2026-07-29", enabled=False) is False   # --force
    assert rd._skip_baked(tmp_path / "gone.json", "2026-07-29") is False


def test_generated_at_stays_inside_the_first_512_bytes(tmp_path):
    """_baked_on only reads the head — a long debate must not push the stamp out of it."""
    big = {"rounds": [{"bull": "x" * 400, "bear": "y" * 400} for _ in range(6)]}
    p = tmp_path / "AAA.json"
    rd._atomic(p, json.dumps(rd.build_bundle("AAA", "A", big, today="2026-07-29"),
                             separators=(",", ":")))
    assert p.stat().st_size > 512 and rd._baked_on(p) == "2026-07-29"


# ------------------------------------------------------------- atomic write
def test_atomic_write_leaves_no_tmp_and_round_trips_utf8(tmp_path):
    p = tmp_path / "AAA.json"
    b = rd.build_bundle("AAA", "Alpha Ltd — ₹ India", _result(), today="2026-07-29")
    rd._atomic(p, json.dumps(b, separators=(",", ":")))
    assert list(tmp_path.iterdir()) == [p]                       # .tmp renamed away
    back = json.loads(p.read_text(encoding="utf-8"))
    assert back["name"] == "Alpha Ltd — ₹ India" and back == b


# ------------------------------------------------- bundle + filings assembly
def test_company_bundle_flattens_fundamental_and_keeps_prices_signal():
    b = rd.company_bundle(_fund(), "AAA", "Alpha")
    assert b["code"] == "AAA" and b["name"] == "Alpha Ltd"
    for k in ("overview", "quarters", "pros", "cons", "analysis"):
        assert k in b                                     # the Screener block is flat now
    assert b["signal"]["label"] == "BUY"
    assert list(b["prices"]) == ["yearwise", "risk", "technical"]
    assert b["upstox_ratios"] == {"current_ratio": 1.4}
    assert b["generated_at"] == "2026-07-28"


def test_company_bundle_tolerates_an_already_flat_bundle():
    flat = {"code": "AAA", "name": "Alpha", "overview": {"Stock P/E": "12"},
            "signal": {"label": "HOLD"}}
    b = rd.company_bundle(flat, "AAA")
    assert b["overview"] == {"Stock P/E": "12"} and b["signal"] == {"label": "HOLD"}
    assert b["prices"] == {} and b["upstox_ratios"] == {}


def test_company_bundle_is_empty_when_there_is_nothing_to_argue_over():
    assert rd.company_bundle(None, "AAA") == {}
    assert rd.company_bundle({}, "AAA") == {}
    assert rd.company_bundle({"generated_at": "2026-07-28", "fundamental": {}}, "AAA") == {}
    assert rd.company_bundle("junk", "AAA") == {}


def test_company_bundle_falls_back_to_the_board_name_then_the_code():
    raw = _fund()
    raw["fundamental"].pop("name")
    assert rd.company_bundle(raw, "AAA", "Board Name")["name"] == "Board Name"
    assert rd.company_bundle(raw, "AAA", "")["name"] == "AAA"


def test_filings_for_serves_both_the_flat_and_nested_reading():
    f = rd.filings_for(_docs())
    assert f["themes"]["guidance"][0]["quote"] == "We guide to 12-14% margin."
    assert f["analysis"]["themes"] == f["themes"]          # renamed key must not lose quotes
    assert f["management_commitments"][0]["timeframe"] == "FY27"
    assert len(f["documents"]) == 1 and f["generated_at"] == "2026-07-27"


def test_filings_for_is_none_when_there_are_no_filings():
    assert rd.filings_for(None) is None
    assert rd.filings_for({}) is None
    assert rd.filings_for({"code": "AAA", "analysis": {}, "documents": []}) is None
    assert rd.filings_for({"documents": [{"url": "u"}]}) is not None


def test_sector_row_matches_case_insensitively_and_copies():
    row = rd.sector_row(TAILWIND, "metals & mining")
    assert row["signal"] == "TAILWIND" and row["n"] == 99
    row["signal"] = "MUTATED"
    assert rd.sector_row(TAILWIND, "Metals & Mining")["signal"] == "TAILWIND"


def test_sector_row_handles_misses_and_junk():
    assert rd.sector_row(TAILWIND, "Banking") is None
    assert rd.sector_row(TAILWIND, "") is None
    assert rd.sector_row(None, "Healthcare") is None
    assert rd.sector_row(TAILWIND["sectors"], "Healthcare")["score"] == -0.4   # bare list


def test_sector_brief_is_the_small_shape_the_site_reads():
    assert rd.sector_brief(rd.sector_row(TAILWIND, "Healthcare")) == {
        "name": "Healthcare", "signal": "HEADWIND", "score": -0.4}
    assert rd.sector_brief(None, "Chemicals") == {"name": "Chemicals", "signal": "", "score": None}
    assert rd.sector_brief(None, "") is None


# --------------------------------------------- tolerant call into debate.py
def test_call_tolerant_drops_a_kwarg_the_callee_never_declared():
    seen = {}

    def run_debate(bundle, *, filings=None, rounds=3):     # no provider=, no sector=
        seen.update(bundle=bundle, filings=filings, rounds=rounds)
        return {"ok": True}

    out = rd.call_tolerant(run_debate, {"code": "AAA"}, filings={"f": 1},
                           sector={"s": 1}, rounds=2, provider="gemini")
    assert out == {"ok": True}
    assert seen == {"bundle": {"code": "AAA"}, "filings": {"f": 1}, "rounds": 2}


def test_call_tolerant_retries_when_the_signature_cannot_be_read():
    """functools.partial/C wrappers hide the signature — the TypeError names the kwarg."""
    calls = []

    class Callee:                       # signature reads as (*args, **kw) -> nothing filtered
        def __call__(self, bundle, *args, **kw):
            calls.append(dict(kw))
            if "provider" in kw:
                raise TypeError("run_debate() got an unexpected keyword argument 'provider'")
            return {"ok": True}

    assert rd.call_tolerant(Callee(), {}, provider="gemini", rounds=3) == {"ok": True}
    assert calls == [{"provider": "gemini", "rounds": 3}, {"rounds": 3}]


def test_call_tolerant_reraises_a_typeerror_raised_inside_the_callee():
    """A bug in debate.py must surface, not be retried — retries cost tokens."""
    def run_debate(bundle, **kw):
        raise TypeError("unsupported operand type(s) for +: 'int' and 'str'")

    with pytest.raises(TypeError):
        rd.call_tolerant(run_debate, {}, rounds=3)


def test_call_tolerant_omits_none_kwargs():
    def run_debate(bundle, **kw):
        return dict(kw)

    assert rd.call_tolerant(run_debate, {}, filings=None, sector=None, rounds=3) == {"rounds": 3}


def test_run_debate_always_returns_a_dict():
    mod = types.SimpleNamespace()
    assert "missing" in rd.run_debate(mod, {})["error"]
    mod.run_debate = lambda bundle, **kw: ["not", "a", "dict"]
    assert "expected dict" in rd.run_debate(mod, {})["error"]
    mod.run_debate = lambda bundle, **kw: _result()
    assert rd.run_debate(mod, {})["verdict"] == "bull edges it"


def test_evidence_for_degrades_to_empty_and_never_raises():
    assert rd.evidence_for(types.SimpleNamespace(), {}) == []          # no evidence_pack yet
    boom = types.SimpleNamespace(evidence_pack=lambda b, **kw: 1 / 0)
    assert rd.evidence_for(boom, {}) == []
    ok = types.SimpleNamespace(evidence_pack=lambda b, **kw: [{"fact": "a"}, "junk", None])
    assert rd.evidence_for(ok, {}) == [{"fact": "a"}]


# --------------------------------------------------------- published bundle
def test_build_bundle_matches_the_contract():
    b = rd.build_bundle("AAA", "Alpha Ltd", _result(rounds=2),
                        sector={"name": "Chemicals", "signal": "TAILWIND", "score": 0.4},
                        evidence=[{"k": "pe"}], rounds=3, today="2026-07-29")
    assert list(b) == ["code", "name", "generated_at", "sector", "debate", "evidence", "_meta"]
    assert (b["code"], b["name"], b["generated_at"]) == ("AAA", "Alpha Ltd", "2026-07-29")
    assert b["sector"]["signal"] == "TAILWIND"
    assert list(b["debate"]) == ["rounds", "verdict"]          # _meta/error lifted out
    assert b["evidence"] == [{"k": "pe"}]
    assert b["_meta"] == {"model": "gemini-2.5-flash", "provider": "gemini", "rounds": 2,
                          "turns": 2, "points": 3, "evidence_items": 1, "note": ""}


def test_build_bundle_keeps_the_modules_grounding_counts():
    """What was STRUCK from a debate is the evidence it is honest — never drop it."""
    r = _result()
    r["_meta"].update(cites_invalid=4, cites_invalid_ids=["E99"], claims_stripped=4,
                      quotes_unverified=0, turns_dropped=1, model="gemini-2.5-flash")
    m = rd.build_bundle("AAA", "A", r, today="2026-07-29")["_meta"]
    assert m["cites_invalid"] == 4 and m["cites_invalid_ids"] == ["E99"]
    assert m["claims_stripped"] == 4 and m["turns_dropped"] == 1
    assert m["model"] == "gemini-2.5-flash" and m["rounds"] == 2   # ours still win


def test_build_bundle_hoists_evidence_the_debate_module_produced():
    r = dict(_result(), evidence=[{"k": "roce"}, {"k": "de"}])
    b = rd.build_bundle("AAA", "A", r, evidence=[{"k": "ignored"}], today="2026-07-29")
    assert b["evidence"] == [{"k": "roce"}, {"k": "de"}]
    assert "evidence" not in b["debate"]
    assert b["_meta"]["evidence_items"] == 2


def test_build_bundle_keeps_the_error_in_meta_note_and_out_of_the_debate():
    b = rd.build_bundle("AAA", "A", {"error": "model call failed: 429", "rounds": []},
                        today="2026-07-29")
    assert "error" not in b and "error" not in b["debate"]
    assert b["_meta"]["note"] == "model call failed: 429"
    assert b["_meta"]["points"] == 0                          # -> keep last good
    assert b["name"] == "A"


def test_build_bundle_survives_a_none_result():
    b = rd.build_bundle("AAA", "", None, today="2026-07-29")
    assert b["debate"] == {} and b["evidence"] == [] and b["name"] == "AAA"
    assert b["_meta"]["rounds"] == 0 and b["_meta"]["points"] == 0


def test_build_bundle_records_rounds_actually_run_not_requested():
    b = rd.build_bundle("AAA", "A", _result(rounds=1), rounds=5, today="2026-07-29")
    assert b["_meta"]["rounds"] == 1
    b2 = rd.build_bundle("AAA", "A", {"verdict": "x", "rounds_run": 2}, rounds=5,
                         today="2026-07-29")
    assert b2["_meta"]["rounds"] == 2
    empty = rd.build_bundle("AAA", "A", {"rounds": []}, rounds=5, today="2026-07-29")
    assert empty["_meta"]["rounds"] == 0          # nothing ran, whatever --rounds said


def test_rounds_are_not_double_counted_from_a_flat_turn_list():
    """debate.py returns one TURN per side per round in a flat "rounds" list.

    len() is therefore the turn count; publishing it as "rounds" would double
    every number an operator reads to reason about spend.
    """
    turns = [{"round": 1, "side": "bull", "text": "a", "cites": ["E1"]},
             {"round": 1, "side": "bear", "text": "b", "cites": ["E2"]},
             {"round": 2, "side": "bull", "text": "c", "cites": ["E1"]},
             {"round": 2, "side": "bear", "text": "d", "cites": ["E3"]}]
    b = rd.build_bundle("AAA", "A", {"rounds": turns, "scorecard": {}}, rounds=2,
                        today="2026-07-29")
    assert b["_meta"]["rounds"] == 2 and b["_meta"]["turns"] == 4


def test_rounds_run_falls_back_to_the_list_length_without_round_numbers():
    assert rd.rounds_run({"rounds": [{"bull": "x"}, {"bull": "y"}]}) == 2
    assert rd.rounds_run({"turns": [1, 2, 3]}) == 3
    assert rd.rounds_run({}, requested=3, points=2) == 3      # ran, but did not say how often
    assert rd.rounds_run({}, requested=3, points=0) == 0      # produced nothing -> ran nothing
    assert rd.rounds_run(None) == 0


# ------------------------------------------------- keep-last-good accounting
def test_debate_points_counts_argument_and_verdict_content():
    assert rd.debate_points(_result(rounds=2)) == 3                 # 2 rounds + verdict
    assert rd.debate_points({"bull": ["a", "b"], "bear": ["c"]}) == 3
    assert rd.debate_points({"verdict": {"label": "BUY"}}) == 1


def test_debate_points_is_zero_for_an_empty_or_broken_run():
    assert rd.debate_points({}) == 0
    assert rd.debate_points(None) == 0
    assert rd.debate_points({"rounds": [], "verdict": ""}) == 0
    assert rd.debate_points({"error": "boom"}) == 0
    assert rd.debate_points({"rounds": 3}) == 0          # a COUNT is not content
    assert rd.debate_points({"rounds": [None, "", {}]}) == 0


# ------------------------------------------------------------ the bake loop
def _fake_debate(run=None, pack=None):
    mod = types.ModuleType("earnings_intel.data.debate")
    mod.run_debate = run or (lambda bundle, **kw: _result())
    mod.evidence_pack = pack or (lambda bundle, **kw: [{"k": "pe", "v": 48.6}])
    return mod


def _bake(monkeypatch, tmp_path, *, board=BOARD, run=None, pack=None, argv=(),
          providers=("gemini",), fundamentals=("AAA", "BBB", "CCC")):
    """Run main() against a throwaway docs/data tree. No network, no key."""
    from earnings_intel import data as data_pkg
    mod = _fake_debate(run, pack)
    monkeypatch.setitem(sys.modules, "earnings_intel.data.debate", mod)
    monkeypatch.setattr(data_pkg, "debate", mod, raising=False)
    monkeypatch.setattr(rd, "have_llm", lambda: list(providers))
    monkeypatch.setattr(rd.time, "sleep", lambda *_a: None)

    (tmp_path / "fundamental").mkdir(exist_ok=True)
    (tmp_path / "docs").mkdir(exist_ok=True)
    for code in fundamentals:
        (tmp_path / "fundamental" / f"{code}.json").write_text(
            json.dumps(_fund(code, f"{code} Ltd")), encoding="utf-8")
    (tmp_path / "docs" / "AAA.json").write_text(json.dumps(_docs()), encoding="utf-8")
    (tmp_path / "technofunda.json").write_text(json.dumps(board), encoding="utf-8")
    (tmp_path / "sector_tailwind.json").write_text(json.dumps(TAILWIND), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [
        "refresh_debate.py",
        "--board", str(tmp_path / "technofunda.json"),
        "--fundamental", str(tmp_path / "fundamental"),
        "--docs", str(tmp_path / "docs"),
        "--sectors", str(tmp_path / "sector_tailwind.json"),
        "--out", str(tmp_path / "out"), *argv])
    return rd.main(), tmp_path / "out"


def test_bake_writes_one_file_per_company(monkeypatch, tmp_path):
    rc, out = _bake(monkeypatch, tmp_path)
    assert rc == 0
    assert baked(out) == ["AAA", "BBB", "CCC"]
    # the bake also publishes the coverage index the page reads before fetching
    idx = json.loads((out / "index.json").read_text())
    assert idx["count"] == 3 and {r["code"] for r in idx["codes"]} == {"AAA", "BBB", "CCC"}
    b = json.loads((out / "BBB.json").read_text(encoding="utf-8"))
    assert b["code"] == "BBB" and b["name"] == "BBB Ltd"
    assert b["debate"]["verdict"] == "bull edges it"
    assert b["sector"] == {"name": "Healthcare", "signal": "HEADWIND", "score": -0.4}
    assert b["evidence"] == [{"k": "pe", "v": 48.6}]
    assert b["generated_at"] == __import__("time").strftime("%Y-%m-%d")


def test_bake_hands_the_model_the_bundle_filings_and_sector_row(monkeypatch, tmp_path):
    seen = {}

    def run(bundle, **kw):
        seen[bundle.get("code")] = (bundle, kw)
        return _result()

    _bake(monkeypatch, tmp_path, run=run)
    bundle, kw = seen["AAA"]
    assert bundle["overview"] == {"Stock P/E": "48.6"} and bundle["signal"]["label"] == "BUY"
    assert kw["filings"]["themes"]["guidance"][0]["url"] == "https://ex.com/t.pdf"
    assert kw["rounds"] == 3                                                  # default
    # CCC is "Metals & Mining" on the board -> that whole tailwind row goes to the model
    assert seen["CCC"][1]["sector"]["signal"] == "TAILWIND"
    assert seen["CCC"][1]["sector"]["n"] == 99
    # CCC has no docs/<CODE>.json — the debate still runs, just without quotes.
    # AAA is "Chemicals", which has no tailwind row. Both absences are DROPPED
    # kwargs rather than explicit None, so debate.py's own defaults apply.
    assert "filings" not in seen["CCC"][1]
    assert "sector" not in seen["AAA"][1]


def test_bake_honours_the_rounds_flag(monkeypatch, tmp_path):
    seen = []
    _bake(monkeypatch, tmp_path, argv=["--rounds", "2", "--top", "1"],
          run=lambda bundle, **kw: seen.append(kw["rounds"]) or _result())
    assert seen == [2]


def test_bake_skips_companies_already_debated_today_by_default(monkeypatch, tmp_path):
    """The priced board must not pay twice in one day just because the job re-ran."""
    calls = []
    _bake(monkeypatch, tmp_path, run=lambda b, **kw: calls.append(b["code"]) or _result())
    assert len(calls) == 3
    calls.clear()
    rc, out = _bake(monkeypatch, tmp_path,
                    run=lambda b, **kw: calls.append(b["code"]) or _result())
    assert calls == [] and rc == 0                       # every file already stamped today
    assert len(baked(out)) == 3


def test_force_re_debates_what_was_already_baked_today(monkeypatch, tmp_path):
    calls = []
    _bake(monkeypatch, tmp_path, run=lambda b, **kw: calls.append(b["code"]) or _result())
    calls.clear()
    _bake(monkeypatch, tmp_path, argv=["--force"],
          run=lambda b, **kw: calls.append(b["code"]) or _result())
    assert sorted(calls) == ["AAA", "BBB", "CCC"]


def test_stale_files_are_re_debated(monkeypatch, tmp_path):
    out = tmp_path / "out"; out.mkdir()
    (out / "AAA.json").write_text('{"code":"AAA","generated_at":"2020-01-01","debate":{}}',
                                  encoding="utf-8")
    calls = []
    _bake(monkeypatch, tmp_path, run=lambda b, **kw: calls.append(b["code"]) or _result())
    assert "AAA" in calls
    assert json.loads((out / "AAA.json").read_text(encoding="utf-8"))["debate"]["verdict"]


def test_bake_never_overwrites_a_good_debate_with_an_empty_one(monkeypatch, tmp_path):
    _bake(monkeypatch, tmp_path)
    good = (tmp_path / "out" / "AAA.json").read_text(encoding="utf-8")
    rc, out = _bake(monkeypatch, tmp_path, argv=["--force"],
                    run=lambda b, **kw: {"rounds": [], "error": "model call failed"})
    assert rc == 0                                       # only 3 attempted — under the guard
    assert (out / "AAA.json").read_text(encoding="utf-8") == good
    assert len(baked(out)) == 3                          # yesterday's three all survive


def test_bake_skips_companies_with_no_fundamental_bundle(monkeypatch, tmp_path):
    calls = []
    rc, out = _bake(monkeypatch, tmp_path, fundamentals=("BBB",),
                    run=lambda b, **kw: calls.append(b["code"]) or _result())
    assert calls == ["BBB"]                              # never pays for an evidence-free debate
    assert baked(out) == ["BBB"]
    assert rc == 0                                       # not attempted != failed


def test_bake_exits_non_zero_when_the_whole_model_chain_is_down(monkeypatch, tmp_path):
    """5+ attempted and NOT ONE debate is an outage, not silence."""
    board = [dict(BOARD[0], code=f"C{i}", composite=i) for i in range(6)]
    codes = [r["code"] for r in board]
    rc, out = _bake(monkeypatch, tmp_path, board=board, fundamentals=codes,
                    run=lambda b, **kw: {"error": "no credentialled provider answered"})
    assert rc == 1
    assert baked(out) == []


def test_bake_exits_non_zero_when_every_company_raises(monkeypatch, tmp_path):
    board = [dict(BOARD[0], code=f"C{i}", composite=i) for i in range(6)]
    codes = [r["code"] for r in board]

    def boom(bundle, **kw):
        raise RuntimeError("HTTP 429")

    rc, _ = _bake(monkeypatch, tmp_path, board=board, fundamentals=codes, run=boom)
    assert rc == 1


def test_a_few_quiet_companies_do_not_fail_the_bake(monkeypatch, tmp_path):
    """Under 5 attempts, empty is 'nothing to say' — that must stay exit 0."""
    rc, out = _bake(monkeypatch, tmp_path, run=lambda b, **kw: {"rounds": []})
    assert rc == 0 and baked(out) == []


def test_one_success_among_failures_is_not_an_outage(monkeypatch, tmp_path):
    board = [dict(BOARD[0], code=f"C{i}", composite=i) for i in range(6)]
    codes = [r["code"] for r in board]

    def run(bundle, **kw):
        if bundle["code"] == "C5":
            return _result()
        raise RuntimeError("HTTP 429")

    rc, out = _bake(monkeypatch, tmp_path, board=board, fundamentals=codes, run=run)
    assert rc == 0 and baked(out) == ["C5"]


def test_bake_exits_zero_and_calls_nothing_without_llm_credentials(monkeypatch, tmp_path):
    calls = []
    rc, out = _bake(monkeypatch, tmp_path, providers=(),
                    run=lambda b, **kw: calls.append(b) or _result())
    assert rc == 0 and calls == [] and not out.exists()


def test_bake_exits_zero_when_the_debate_module_is_not_written_yet(monkeypatch, tmp_path):
    """A rollout where scripts land before earnings_intel/data/debate.py must not go red."""
    import builtins
    real = builtins.__import__

    def no_debate(name, *a, **kw):
        if name == "earnings_intel.data.debate" or (name == "earnings_intel.data"
                                                    and "debate" in (a[2] if len(a) > 2 else ())):
            raise ImportError("No module named 'earnings_intel.data.debate'")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_debate)
    rc, out = _bake(monkeypatch, tmp_path)
    assert rc == 0 and not out.exists()


def test_bake_respects_top_and_extra_codes(monkeypatch, tmp_path):
    calls = []
    _bake(monkeypatch, tmp_path, argv=["--top", "1", "--codes", "CCC"],
          run=lambda b, **kw: calls.append(b["code"]) or _result())
    assert calls == ["BBB", "CCC"]                       # top-1 by composite, then --codes


def test_bake_stops_at_the_time_budget(monkeypatch, tmp_path):
    board = [dict(BOARD[0], code=f"C{i}", composite=i) for i in range(6)]
    codes = [r["code"] for r in board]
    clock = {"t": 0.0}
    monkeypatch.setattr(rd.time, "time", lambda: clock["t"])

    def slow(bundle, **kw):
        clock["t"] += 120.0                              # two minutes per company
        return _result()

    rc, out = _bake(monkeypatch, tmp_path, board=board, fundamentals=codes,
                    argv=["--max-minutes", "3"], run=slow)
    assert rc == 0
    assert len(baked(out)) == 2                          # stopped once past 3 minutes


# ------------------------------------------------- coverage pass (--skip-any)
def test_extend_universe_appends_on_disk_codes_the_board_never_ranked(tmp_path):
    """The board ranks a few hundred names; the platform publishes thousands."""
    for c in ("AAA", "BBB", "CCC", "ZZZ"):
        (tmp_path / f"{c}.json").write_text("{}")
    out = rd.extend_universe(["BBB"], tmp_path, 3)
    assert out[0] == "BBB"                      # board order preserved first
    assert set(out) == {"BBB", "AAA", "CCC"}    # then disk, deduped, capped


def test_extend_universe_never_debates_the_index_file(tmp_path):
    (tmp_path / "index.json").write_text("{}")
    (tmp_path / "TCS.json").write_text("{}")
    assert rd.extend_universe([], tmp_path, 50) == ["TCS"]


def test_extend_universe_respects_the_cap_and_a_missing_directory(tmp_path):
    for c in ("A", "B", "C"):
        (tmp_path / f"{c}.json").write_text("{}")
    assert len(rd.extend_universe(["X"], tmp_path, 2)) == 2
    assert rd.extend_universe(["X"], tmp_path, 0) == ["X"]
    assert rd.extend_universe(["X"], tmp_path / "nope", 9) == ["X"]


def test_skip_any_skips_a_debate_from_ANY_day_not_just_today(tmp_path):
    """The coverage pass must never pay twice for a company it already argued."""
    f = tmp_path / "OLD.json"
    f.write_text('{"code":"OLD","generated_at":"2026-01-04","debate":{}}')
    assert rd._skip_baked(f, "2026-07-29", True, False) is False   # daily: re-bake
    assert rd._skip_baked(f, "2026-07-29", True, True) is True     # coverage: skip


def test_skip_any_still_bakes_a_company_with_no_file_or_no_stamp(tmp_path):
    assert rd._skip_baked(tmp_path / "MISSING.json", "2026-07-29", True, True) is False
    junk = tmp_path / "JUNK.json"
    junk.write_text('{"code":"JUNK"}')          # legacy file, no stamp -> bake once
    assert rd._skip_baked(junk, "2026-07-29", True, True) is False


def test_write_index_lists_what_is_on_disk_with_each_stamp(tmp_path):
    (tmp_path / "TCS.json").write_text('{"code":"TCS","generated_at":"2026-07-29","debate":{}}')
    (tmp_path / "LT.json").write_text('{"code":"LT","generated_at":"2026-07-28","debate":{}}')
    n = rd.write_index(tmp_path)
    idx = json.loads((tmp_path / "index.json").read_text())
    assert n == idx["count"] == 2
    assert {r["code"]: r["generated_at"] for r in idx["codes"]} == {
        "TCS": "2026-07-29", "LT": "2026-07-28"}


def test_write_index_never_lists_itself_and_survives_a_rerun(tmp_path):
    (tmp_path / "TCS.json").write_text('{"code":"TCS","generated_at":"2026-07-29","debate":{}}')
    rd.write_index(tmp_path)
    rd.write_index(tmp_path)                    # index.json now exists on disk
    idx = json.loads((tmp_path / "index.json").read_text())
    assert [r["code"] for r in idx["codes"]] == ["TCS"]
    assert not list(tmp_path.glob("*.tmp"))


def test_write_index_on_an_empty_directory_is_an_honest_zero(tmp_path):
    assert rd.write_index(tmp_path) == 0
    assert json.loads((tmp_path / "index.json").read_text())["codes"] == []
