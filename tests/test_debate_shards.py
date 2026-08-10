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


# ------------------------------------------- one provider pinned per shard

def test_each_shard_is_pinned_to_one_provider():
    """N credentials must become N independent quotas. Eight shards on ONE
    provider just hit that provider's rate limit eight times faster."""
    out = shard([f"C{i}" for i in range(40)], 6,
                providers=["gemini", "deepseek", "mistral", "ollama"])
    assert [s["provider"] for s in out] == \
        ["gemini", "deepseek", "mistral", "ollama", "gemini", "deepseek"]


def test_more_providers_than_shards_is_not_an_error():
    out = shard([f"C{i}" for i in range(10)], 2,
                providers=["a", "b", "c", "d", "e"])
    assert [s["provider"] for s in out] == ["a", "b"]


def test_no_providers_means_first_credentialled_as_before():
    out = shard(["A", "B", "C"], 2)
    assert all(s["provider"] == "" for s in out)


def test_blank_provider_names_are_ignored_not_assigned():
    """An unset secret produces an empty entry; a shard must not be pinned to
    the empty string while real providers sit unused."""
    out = shard([f"C{i}" for i in range(6)], 3, providers=["gemini", "", "  ", "ollama"])
    assert sorted({s["provider"] for s in out}) == ["gemini", "ollama"]


def test_sharding_stays_disjoint_with_providers_attached():
    codes = [f"C{i}" for i in range(37)]
    out = shard(codes, 5, providers=["x", "y"])
    seen = [c for s in out for c in s["codes"].split(",")]
    assert sorted(seen) == sorted(codes)
    assert len(seen) == len(set(seen))


# ------------------------------------- the planner must need no dependencies

def test_planner_runs_without_numpy_or_requests(tmp_path):
    """The plan job installs NOTHING -- that is its whole point, it reads JSON
    and prints a matrix in ten seconds. When this script imported
    `earnings_intel.data.freshness` it executed earnings_intel/__init__.py,
    which pulls in the pipeline and finally numpy, and every scheduled run died
    at the first step with ModuleNotFoundError while the rest of the workflow
    was skipped behind it. Nothing surfaced except a red tick.
    """
    import json
    import subprocess
    import sys
    import textwrap

    fdir, ddir = tmp_path / "f", tmp_path / "d"
    fdir.mkdir()
    ddir.mkdir()
    (fdir / "AAA.json").write_text(
        json.dumps({"fundamental": {"overview": {"Market Cap": "100"}}}), encoding="utf-8")

    harness = tmp_path / "run.py"
    harness.write_text(textwrap.dedent(f"""
        import sys

        class _Block:
            BLOCKED = {{"numpy", "pandas", "requests", "bs4", "lxml", "curl_cffi"}}
            def find_module(self, name, path=None):
                return self if name.split(".")[0] in self.BLOCKED else None
            def load_module(self, name):
                raise ImportError("blocked: " + name)

        sys.meta_path.insert(0, _Block())
        sys.argv = ["debate_shards.py", "--shards", "2",
                    "--fundamental", {str(fdir)!r},
                    "--debate", {str(ddir)!r}]
        exec(open({str(ROOT / "scripts" / "debate_shards.py")!r}, encoding="utf-8").read(),
             {{"__name__": "__main__",
               "__file__": {str(ROOT / "scripts" / "debate_shards.py")!r}}})
    """), encoding="utf-8")

    proc = subprocess.run([sys.executable, str(harness)], capture_output=True, text=True)
    assert proc.returncode == 0, f"planner needs a dependency it will not have:\n{proc.stderr}"
    assert "AAA" in proc.stdout


def test_planner_emits_needs_ollama(tmp_path):
    """The warm job is gated on this. When it was missing the gate read false,
    warm was skipped, and bake was skipped behind it -- a green run that baked
    nothing, whose only symptom was the backlog not moving."""
    import json
    import subprocess
    import sys

    fdir, ddir = tmp_path / "f", tmp_path / "d"
    fdir.mkdir()
    ddir.mkdir()
    for code in ("AAA", "BBB"):
        (fdir / f"{code}.json").write_text(
            json.dumps({"fundamental": {"overview": {"Market Cap": "100"}}}), encoding="utf-8")

    def run(providers):
        out = tmp_path / f"out_{providers or 'none'}.txt"
        out.write_text("", encoding="utf-8")
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "debate_shards.py"),
             "--shards", "2", "--providers", providers,
             "--fundamental", str(fdir), "--debate", str(ddir),
             "--github-output", str(out)],
            capture_output=True, text=True, check=True)
        return dict(l.split("=", 1) for l in out.read_text(encoding="utf-8").splitlines() if "=" in l)

    local = run("gemini,ollama")
    assert local["needs_ollama"] == "yes", "warm would be skipped with an ollama shard queued"
    assert local["remaining"] == "2"

    hosted = run("gemini,deepseek")
    assert hosted["needs_ollama"] == "no", "2GB of weights pulled for nothing"
