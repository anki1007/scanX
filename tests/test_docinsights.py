"""Document-insight bake (scripts/refresh_docinsights.py) — pure functions, no network.

Covers the three things the bake can silently get wrong: which companies are
picked, whether --skip-existing trusts the stamp INSIDE the file (mtime breaks in
CI), and the shape/atomicity of what lands on disk.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import refresh_docinsights as rd            # noqa: E402
from earnings_intel.data import docanalysis as da   # noqa: E402

BOARD = [
    {"code": "AAA", "name": "Alpha Ltd", "composite": 51},
    {"code": "BBB", "name": "Beta Ltd", "composite": 78},
    {"code": "CCC", "name": "Gamma Ltd", "composite": 65},
    {"code": "", "name": "no code", "composite": 99},
]


def _fact(claim="Guided to 12-14% EBITDA margin for FY27",
          quote="We are guiding to a 12-14% EBITDA margin for FY27."):
    return {"claim": claim, "quote": quote, "doc_kind": "concall_transcript",
            "doc_date": "2026-05-01", "url": "https://ex.com/t.pdf"}


def _analysis(facts=1, commitments=0, meta=True):
    themes = {t: [] for t in da.THEMES}
    themes["guidance"] = [_fact(f"claim {i}", f"quote number {i} verbatim") for i in range(facts)]
    out = {"summary": "Management said things.", "themes": themes,
           "management_commitments": [dict(_fact(), timeframe="FY27")] * commitments,
           "coverage": {"docs_analysed": 2, "kinds": ["concall_transcript", "concall_ppt"]}}
    if meta:
        out["_meta"] = {"model": "gemini-2.5-flash", "grounded": True, "facts_kept": facts,
                        "facts_dropped_ungrounded": 3, "note": "grounded"}
    return out


DOCS = [{"kind": "concall_transcript", "date": "2026-05-01", "title": "Q4 FY26 Transcript",
         "url": "https://ex.com/t.pdf", "source": "screener"}]


# ------------------------------------------------------------------ universe
def test_universe_sorts_by_composite_and_caps_at_top():
    codes, names = rd.universe(BOARD, top=2)
    assert codes == ["BBB", "CCC"]                 # 78, 65 — not board order
    assert names["BBB"] == "Beta Ltd"


def test_universe_appends_extra_codes_after_the_board():
    codes, _ = rd.universe(BOARD, top=1, codes="TATAMOTORS, lt")
    assert codes == ["BBB", "TATAMOTORS", "LT"]


def test_universe_dedupes_codes_already_in_the_board():
    codes, _ = rd.universe(BOARD, top=3, codes="ccc,AAA")
    assert codes == ["BBB", "CCC", "AAA"]


def test_universe_drops_blank_codes_and_non_dict_rows():
    codes, _ = rd.universe(BOARD + ["junk", None, {"name": "x"}], top=10)
    assert codes == ["BBB", "CCC", "AAA"]


def test_universe_top_zero_is_codes_only():
    codes, _ = rd.universe(BOARD, top=0, codes="LT")
    assert codes == ["LT"]


def test_universe_empty_board():
    assert rd.universe([], top=100) == ([], {})
    assert rd.universe(None, top=100, codes="LT")[0] == ["LT"]


def test_universe_missing_composite_sorts_last():
    codes, _ = rd.universe([{"code": "X"}, {"code": "Y", "composite": 10}], top=2)
    assert codes == ["Y", "X"]


def test_split_codes_normalises_and_dedupes():
    assert rd.split_codes(" tatamotors ,LT,,lt; RELIANCE ") == ["TATAMOTORS", "LT", "RELIANCE"]
    assert rd.split_codes("") == [] and rd.split_codes(None) == []


def test_read_board_recovers_truncated_write(tmp_path):
    p = tmp_path / "technofunda.json"
    good = json.dumps(BOARD[:3])
    p.write_bytes(good[:-14].encode() + b"\x00" * 8)      # truncated mid-object + null pad
    rows = rd._read_board(p)
    assert [r["code"] for r in rows] == ["AAA", "BBB"]
    assert rd._read_board(tmp_path / "missing.json") == []


# -------------------------------------------------- generated_at skip logic
def test_baked_on_reads_stamp_from_inside_the_file(tmp_path):
    p = tmp_path / "AAA.json"
    rd._atomic(p, json.dumps(rd.build_bundle("AAA", "Alpha Ltd", DOCS, _analysis(), "2026-07-27"),
                             separators=(",", ":")))
    assert rd._baked_on(p) == "2026-07-27"


def test_baked_on_is_blank_for_legacy_and_unreadable_files(tmp_path):
    legacy = tmp_path / "L.json"; legacy.write_text('{"code":"L","documents":[]}', encoding="utf-8")
    assert rd._baked_on(legacy) == ""
    assert rd._baked_on(tmp_path / "nope.json") == ""


def test_baked_on_ignores_mtime_not_content(tmp_path):
    """A fresh CI checkout stamps every file 'today' — the date must come from the JSON."""
    p = tmp_path / "AAA.json"
    rd._atomic(p, json.dumps(rd.build_bundle("AAA", "A", DOCS, _analysis(), "2020-01-02"),
                             separators=(",", ":")))
    os_now = __import__("time").time()
    __import__("os").utime(p, (os_now, os_now))           # "checked out just now"
    assert rd._baked_on(p) == "2020-01-02"
    assert rd._skip_baked(p, "2026-07-27") is False       # stale -> rebake


def test_skip_baked_only_when_stamp_matches_today(tmp_path):
    p = tmp_path / "AAA.json"
    rd._atomic(p, json.dumps(rd.build_bundle("AAA", "A", DOCS, _analysis(), "2026-07-27"),
                             separators=(",", ":")))
    assert rd._skip_baked(p, "2026-07-27") is True
    assert rd._skip_baked(p, "2026-07-28") is False
    assert rd._skip_baked(p, "2026-07-27", enabled=False) is False   # flag off -> always rebake
    assert rd._skip_baked(tmp_path / "gone.json", "2026-07-27") is False


# ------------------------------------------------------------- atomic write
def test_atomic_write_leaves_no_tmp_and_round_trips_utf8(tmp_path):
    p = tmp_path / "AAA.json"
    bundle = rd.build_bundle("AAA", "Alpha Ltd — ₹ India", DOCS, _analysis(), "2026-07-27")
    rd._atomic(p, json.dumps(bundle, separators=(",", ":")))
    assert list(tmp_path.iterdir()) == [p]                       # .tmp renamed away
    back = json.loads(p.read_text(encoding="utf-8"))
    assert back["name"] == "Alpha Ltd — ₹ India"
    assert back == bundle


def test_atomic_write_replaces_previous_bundle(tmp_path):
    p = tmp_path / "AAA.json"
    rd._atomic(p, '{"generated_at":"2020-01-01"}')
    rd._atomic(p, '{"generated_at":"2026-07-27"}')
    assert rd._baked_on(p) == "2026-07-27"
    assert not (tmp_path / "AAA.json.tmp").exists()


# ------------------------------------------------------------ bundle shape
def test_build_bundle_matches_the_contract():
    b = rd.build_bundle("AAA", "Alpha Ltd", DOCS, _analysis(facts=2, commitments=1), "2026-07-27")
    assert list(b) == ["code", "name", "generated_at", "documents", "analysis", "_meta"]
    assert (b["code"], b["name"], b["generated_at"]) == ("AAA", "Alpha Ltd", "2026-07-27")
    assert b["documents"] == DOCS
    assert list(b["analysis"]) == ["summary", "themes", "management_commitments", "coverage"]
    assert list(b["analysis"]["themes"]) == list(da.THEMES)      # all seven, contract order
    assert len(b["analysis"]["themes"]["guidance"]) == 2
    assert b["analysis"]["coverage"] == {"docs_analysed": 2,
                                         "kinds": ["concall_transcript", "concall_ppt"]}
    assert b["_meta"] == {"model": "gemini-2.5-flash", "grounded": True, "facts_kept": 2,
                          "facts_dropped_ungrounded": 3, "note": "grounded"}
    fact = b["analysis"]["themes"]["guidance"][0]
    assert set(fact) == {"claim", "quote", "doc_kind", "doc_date", "url"}


def test_build_bundle_falls_back_to_code_when_name_missing():
    b = rd.build_bundle("AAA", "", DOCS, _analysis(), "2026-07-27")
    assert b["name"] == "AAA"


def test_build_bundle_keeps_error_in_meta_note_and_drops_it_from_analysis():
    bad = da._empty("no analysable documents", "gemini-2.5-flash")
    b = rd.build_bundle("AAA", "Alpha Ltd", [], bad, "2026-07-27")
    assert "error" not in b["analysis"] and "error" not in b
    assert "no analysable documents" in b["_meta"]["note"]
    assert b["_meta"]["facts_kept"] == 0
    assert list(b["analysis"]["themes"]) == list(da.THEMES)


def test_build_bundle_fills_missing_themes_and_meta():
    b = rd.build_bundle("AAA", "Alpha", DOCS, {"summary": "s"}, "2026-07-27")
    assert all(b["analysis"]["themes"][t] == [] for t in da.THEMES)
    assert b["_meta"]["grounded"] is True and b["_meta"]["model"] == ""
    assert b["analysis"]["coverage"] == {"docs_analysed": 0, "kinds": []}


def test_build_bundle_survives_none_analysis():
    b = rd.build_bundle("AAA", "Alpha", None, None, "2026-07-27")
    assert b["analysis"]["summary"] == "" and b["documents"] == []


# ------------------------------------------- keep-last-good (fact accounting)
def test_fact_count_counts_themes_and_commitments():
    assert rd.fact_count(_analysis(facts=3, commitments=2)) == 5
    assert rd.fact_count(_analysis(facts=0)) == 0
    assert rd.fact_count(da._empty("no key", "m")) == 0
    assert rd.fact_count(None) == 0 and rd.fact_count({"themes": "junk"}) == 0
