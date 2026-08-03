"""Re-argue a company when its FILINGS move, not when the calendar does.

Before this the debate board had two modes and neither asked about the numbers:
the top 100 were re-argued every 7 days whether or not anything had happened,
and the other ~5,400 were baked once and never revisited. A company could
publish a quarter that halved its margin and its bull/bear case would sit
there, dated and confident, indefinitely.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from earnings_intel.data.freshness import (  # noqa: E402
    FINGERPRINT_KEY, fingerprint, is_stale,
)


def _bundle(headers, sales, profit, eps=None, price="100"):
    return {"fundamental": {
        "overview": {"Current Price": price},
        "quarters": {"headers": list(headers),
                     "rows": {"Sales": list(sales), "Net Profit": list(profit),
                              "EPS": list(eps or profit)}},
    }}


Q = ["Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026"]
BASE = _bundle(Q, [100, 110, 120, 130], [10, 11, 12, 13])


# ------------------------------------------------------ what must re-trigger

def test_a_new_quarter_makes_the_debate_stale():
    """The single most important trigger there is."""
    after = _bundle(Q + ["Sep 2026"], [100, 110, 120, 130, 145], [10, 11, 12, 13, 15])
    debate = {FINGERPRINT_KEY: fingerprint(BASE)}
    assert is_stale(debate, after) is True


def test_restated_numbers_make_the_debate_stale():
    """Same periods, different figures -- a restatement."""
    restated = _bundle(Q, [100, 110, 120, 95], [10, 11, 12, 4])
    debate = {FINGERPRINT_KEY: fingerprint(BASE)}
    assert is_stale(debate, restated) is True


def test_a_company_never_argued_is_due():
    assert is_stale(None, BASE) is True


# --------------------------------------------------- what must NOT re-trigger

def test_an_unchanged_company_is_left_alone():
    debate = {FINGERPRINT_KEY: fingerprint(BASE)}
    assert is_stale(debate, BASE) is False


def test_a_moving_share_price_does_not_re_argue_the_universe_nightly():
    """The bundle carries a live price and a bake timestamp, both of which
    change every day. Hashing those would mark all 5,499 companies stale every
    night -- the expensive version of the bug this fixes."""
    moved = _bundle(Q, [100, 110, 120, 130], [10, 11, 12, 13], price="188.40")
    moved["generated_at"] = "2026-08-03"
    assert fingerprint(moved) == fingerprint(BASE)


def test_an_older_debate_without_a_fingerprint_is_not_re_run():
    """Treating 'cannot tell' as 'changed' would re-argue the whole back
    catalogue the first time this ships, at real cost, for no new information."""
    assert is_stale({"code": "X", "generated_at": "2026-07-01"}, BASE) is False


def test_an_unreadable_bundle_does_not_force_a_re_run():
    assert is_stale({FINGERPRINT_KEY: "abc123"}, None) is False
    assert is_stale({FINGERPRINT_KEY: "abc123"}, {}) is False


# ------------------------------------------------------------- fingerprint

def test_fingerprint_is_stable_and_short():
    a, b = fingerprint(BASE), fingerprint(BASE)
    assert a == b and 0 < len(a) <= 16


def test_no_data_gives_no_fingerprint():
    for junk in (None, {}, [], "x", {"fundamental": {}}, {"fundamental": []}):
        assert fingerprint(junk) == ""


def test_junk_never_raises():
    for junk in (None, {}, [], "x", {"fundamental": {"quarters": "nope"}},
                 {"fundamental": {"quarters": {"headers": None, "rows": None}}}):
        fingerprint(junk)
        is_stale({FINGERPRINT_KEY: "x"}, junk)


# --------------------------------------------- the planner honours all this

def test_the_cloud_planner_requeues_a_company_whose_filings_moved(tmp_path):
    import json

    from debate_shards import remaining

    fdir, ddir = tmp_path / "f", tmp_path / "d"
    fdir.mkdir(), ddir.mkdir()

    moved = _bundle(Q + ["Sep 2026"], [100, 110, 120, 130, 145], [10, 11, 12, 13, 15])
    (fdir / "MOVED.json").write_text(json.dumps(moved), encoding="utf-8")
    (ddir / "MOVED.json").write_text(
        json.dumps({FINGERPRINT_KEY: fingerprint(BASE)}), encoding="utf-8")

    (fdir / "SAME.json").write_text(json.dumps(BASE), encoding="utf-8")
    (ddir / "SAME.json").write_text(
        json.dumps({FINGERPRINT_KEY: fingerprint(BASE)}), encoding="utf-8")

    (fdir / "NEW.json").write_text(json.dumps(BASE), encoding="utf-8")

    due = set(remaining(fdir, ddir))
    assert "MOVED" in due, "a company whose filings moved was not re-queued"
    assert "NEW" in due, "a company never argued was not queued"
    assert "SAME" not in due, "an unchanged company would be argued again"
