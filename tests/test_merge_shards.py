"""Shard merge.

The bug this covers burned roughly fourteen hours of compute per run, four
runs a day, and reported success every time. `cp -n` meant a company already
on disk could never be replaced, so the re-queue path was a closed loop: a
half-baked debate is marked incomplete, a shard re-argues it properly, the
merge refuses to overwrite, it stays half-baked, it is re-queued again.

Loaded by file path, NOT by importing the package: pulling in earnings_intel
from a CI step dragged numpy in through __init__ and killed the job.
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "merge_shards", ROOT / "scripts" / "merge_shards.py")
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)


def _debate(turns=4, rounds=2, evidence=10, note="x"):
    return {"_meta": {"turns": turns, "rounds": rounds,
                      "evidence_items": evidence, "provider": "ollama"},
            "note": note}


def _write(path, bundle):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle), encoding="utf-8")


# ------------------------------------------------------------ the ordering

def test_more_turns_wins():
    assert ms.should_replace(_debate(turns=4), _debate(turns=1))


def test_fewer_turns_loses():
    assert not ms.should_replace(_debate(turns=1), _debate(turns=4))


def test_an_equal_debate_does_not_replace():
    """Ties keep what is on disk, so a replay onto a new tip is idempotent and
    two runs that baked the same company do not fight."""
    assert not ms.should_replace(_debate(), _debate())


def test_evidence_breaks_a_tie_on_length():
    assert ms.should_replace(_debate(evidence=20), _debate(evidence=5))


def test_anything_beats_nothing():
    assert ms.should_replace(_debate(turns=1), None)
    assert not ms.should_replace(None, _debate(turns=1))


def test_completeness_survives_junk():
    for junk in (None, {}, [], "x", 5, {"_meta": "no"},
                 {"_meta": {"turns": "many"}}):
        assert ms.completeness(junk) == (0, 0, 0) or isinstance(
            ms.completeness(junk), tuple)


# --------------------------------------------------------------- the merge

def test_a_rebaked_debate_replaces_the_half_baked_one(tmp_path):
    """SPMLINFRA, exactly as it happened: 1 turn over 1 round on disk, re-argued
    to 3 turns over 2 rounds by a shard, and discarded by cp -n."""
    out = tmp_path / "debate"
    shards = tmp_path / "shards" / "debate-shard-1"
    _write(out / "SPMLINFRA.json", _debate(turns=1, rounds=1, note="half"))
    _write(shards / "SPMLINFRA.json", _debate(turns=3, rounds=2, note="full"))

    stats = ms.merge(tmp_path / "shards", out)
    assert stats["improved"] == 1 and stats["added"] == 0
    assert json.loads((out / "SPMLINFRA.json").read_text())["note"] == "full"


def test_a_new_company_is_added(tmp_path):
    out = tmp_path / "debate"
    shards = tmp_path / "shards" / "debate-shard-0"
    out.mkdir(parents=True)
    _write(shards / "NEWCO.json", _debate())
    stats = ms.merge(tmp_path / "shards", out)
    assert stats["added"] == 1 and stats["improved"] == 0
    assert (out / "NEWCO.json").exists()


def test_a_worse_shard_copy_never_clobbers_a_good_one(tmp_path):
    out = tmp_path / "debate"
    shards = tmp_path / "shards" / "debate-shard-2"
    _write(out / "GOOD.json", _debate(turns=4, note="keep"))
    _write(shards / "GOOD.json", _debate(turns=1, note="worse"))
    stats = ms.merge(tmp_path / "shards", out)
    assert stats["kept"] == 1
    assert json.loads((out / "GOOD.json").read_text())["note"] == "keep"


def test_shard_index_files_are_not_copied(tmp_path):
    """A shard's index describes only that shard; the real one is rebuilt."""
    out = tmp_path / "debate"
    shards = tmp_path / "shards" / "debate-shard-3"
    out.mkdir(parents=True)
    _write(shards / "index.json", {"only": "this shard"})
    _write(shards / "REAL.json", _debate())
    ms.merge(tmp_path / "shards", out)
    assert not (out / "index.json").exists()
    assert (out / "REAL.json").exists()


def test_unreadable_shard_files_are_counted_not_fatal(tmp_path):
    out = tmp_path / "debate"
    shards = tmp_path / "shards" / "debate-shard-4"
    out.mkdir(parents=True)
    (shards).mkdir(parents=True)
    (shards / "BROKEN.json").write_text("{not json", encoding="utf-8")
    _write(shards / "OK.json", _debate())
    stats = ms.merge(tmp_path / "shards", out)
    assert stats["unreadable"] == 1 and stats["added"] == 1


def test_merging_twice_changes_nothing_the_second_time(tmp_path):
    """The push step re-runs this after resetting to origin/main, so it has to
    be idempotent or the replay loop never terminates."""
    out = tmp_path / "debate"
    shards = tmp_path / "shards" / "debate-shard-5"
    out.mkdir(parents=True)
    _write(shards / "A.json", _debate())
    first = ms.merge(tmp_path / "shards", out)
    second = ms.merge(tmp_path / "shards", out)
    assert first["added"] == 1
    assert second["added"] == 0 and second["improved"] == 0


def test_a_missing_shard_directory_is_not_fatal(tmp_path):
    out = tmp_path / "debate"
    out.mkdir(parents=True)
    assert ms.merge(tmp_path / "nope", out)["added"] == 0


def test_total_counts_debates_not_the_index(tmp_path):
    out = tmp_path / "debate"
    out.mkdir(parents=True)
    _write(out / "index.json", {"i": 1})
    _write(out / "A.json", _debate())
    assert ms.merge(tmp_path / "nope", out)["total"] == 1


# ---------------------------------------------------------------- the wiring

def test_the_pipeline_no_longer_refuses_to_overwrite():
    sh = (ROOT / "scripts" / "ci_merge_debates.sh").read_text(encoding="utf-8")
    assert "cp -n" not in sh, "the no-clobber copy is back"
    assert "merge_shards.py" in sh


def test_the_shell_does_not_reference_the_removed_counter():
    """`count` and `before` went with the old arithmetic; under `set -u` a
    leftover reference aborts the merge after the shards are already merged."""
    sh = (ROOT / "scripts" / "ci_merge_debates.sh").read_text(encoding="utf-8")
    assert "$(count)" not in sh and "${before}" not in sh


def test_added_reports_improvements_too():
    """Counting files scored an improved debate as 0, so the commit step said
    "nothing new to publish" and exited green on a discarded run."""
    src = (ROOT / "scripts" / "merge_shards.py").read_text(encoding="utf-8")
    assert "ADDED={stats['added'] + stats['improved']}" in src


def test_the_merger_imports_no_heavy_package():
    """Importing earnings_intel in a CI step pulled numpy through __init__ and
    killed the job. This one is stdlib only."""
    src = (ROOT / "scripts" / "merge_shards.py").read_text(encoding="utf-8")
    for banned in ("earnings_intel", "numpy", "pandas", "requests"):
        assert f"import {banned}" not in src and f"from {banned}" not in src
