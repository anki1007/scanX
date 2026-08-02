"""Sharding the debate backlog for a matrix build."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from debate_shards import remaining, shard  # noqa: E402


def _bundle(mcap):
    return json.dumps({"fundamental": {"overview": {"Market Cap": mcap}}})


def _make(tmp_path, bundles, debates=()):
    fdir = tmp_path / "fundamental"
    ddir = tmp_path / "debate"
    fdir.mkdir()
    ddir.mkdir()
    for code, mcap in bundles.items():
        (fdir / f"{code}.json").write_text(_bundle(mcap), encoding="utf-8")
    for code in debates:
        (ddir / f"{code}.json").write_text("{}", encoding="utf-8")
    return fdir, ddir


def test_only_companies_without_a_debate_are_returned(tmp_path):
    fdir, ddir = _make(tmp_path, {"A": 100, "B": 200, "C": 300}, debates=["B"])
    assert set(remaining(fdir, ddir)) == {"A", "C"}


def test_largest_company_first(tmp_path):
    fdir, ddir = _make(tmp_path, {"SMALL": "1,000", "BIG": "9,00,000", "MID": "50,000"})
    assert remaining(fdir, ddir) == ["BIG", "MID", "SMALL"]


def test_the_index_file_is_not_a_company(tmp_path):
    fdir, ddir = _make(tmp_path, {"A": 1})
    (ddir / "index.json").write_text("{}", encoding="utf-8")
    (fdir / "index.json").write_text("{}", encoding="utf-8")
    assert remaining(fdir, ddir) == ["A"]


def test_an_unreadable_bundle_still_gets_a_turn(tmp_path):
    """It sorts last, but it must not vanish from the universe silently."""
    fdir, ddir = _make(tmp_path, {"GOOD": 100})
    (fdir / "BROKEN.json").write_text("{not json", encoding="utf-8")
    out = remaining(fdir, ddir)
    assert out == ["GOOD", "BROKEN"]


def test_a_missing_debate_directory_means_everything_remains(tmp_path):
    fdir, _ = _make(tmp_path, {"A": 1, "B": 2})
    assert set(remaining(fdir, tmp_path / "nope")) == {"A", "B"}


# ------------------------------------------------------------------ sharding

def test_shards_are_disjoint_and_complete():
    codes = [f"C{i}" for i in range(50)]
    out = shard(codes, 7)
    seen = [c for s in out for c in s["codes"].split(",")]
    assert sorted(seen) == sorted(codes)
    assert len(seen) == len(set(seen)), "a company landed in two shards"


def test_round_robin_spreads_the_big_names():
    """Contiguous blocks would hand shard 0 every megacap. The packs are not
    the same size, so that shard would run hours past the others."""
    codes = [f"C{i}" for i in range(40)]      # already market-cap ordered
    out = shard(codes, 4)
    firsts = [s["codes"].split(",")[0] for s in out]
    assert firsts == ["C0", "C1", "C2", "C3"], "not round robin"
    sizes = {s["n"] for s in out}
    assert max(sizes) - min(sizes) <= 1, "shards are unbalanced"


def test_empty_backlog_produces_no_shards():
    assert shard([], 8) == []


def test_fewer_companies_than_shards_does_not_emit_empty_shards():
    out = shard(["A", "B"], 8)
    assert len(out) == 2
    assert all(s["codes"] for s in out)


def test_per_shard_cap_is_honoured():
    out = shard([f"C{i}" for i in range(100)], 4, per_shard_cap=5)
    assert all(s["n"] == 5 for s in out)


def test_shard_count_is_never_zero():
    out = shard(["A", "B", "C"], 0)
    assert len(out) == 1 and out[0]["n"] == 3
