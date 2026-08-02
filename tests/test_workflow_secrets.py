"""Every credential a step needs must be exported by the workflow that runs it.

A missing `env:` entry does not error in GitHub Actions — the variable simply
resolves to empty, the optional feature latches off, and the run goes green
while quietly producing nothing. That is exactly how the Upstox ratios shipped,
ran daily, and left the health panel showing "n/a"; and how the June price
freeze stayed invisible. These tests make the omission fail locally instead.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WF = ROOT / ".github" / "workflows"

# script -> the ways its credential requirement can be satisfied. A list of
# ALTERNATIVES: the step is fine if ANY ONE of them is fully provided.
NEEDS: dict[str, list[set[str]]] = {
    "refresh_fundamentals.py": [{"UPSTOX_FUNDAMENTAL_ANALYTICS_TOKEN"}],
    "refresh_docinsights.py": [{"GEMINI_API_KEY"}],
    # refresh_debate.py accepts ANY credentialled provider, and with none set it
    # prints "no LLM credentials configured" and exits 0. Green, silent, and no
    # debates: the precise failure shape this file exists to make impossible.
    #
    # Two ways to satisfy it. A hosted key, or a local model -- for which
    # OLLAMA_HOST is the opt-in switch the adapter actually tests, and which is
    # deliberately NOT a secret because there is nothing secret about
    # 127.0.0.1. The cloud debate workflow uses the second.
    "refresh_debate.py": [{"GEMINI_API_KEY"}, {"OLLAMA_HOST"}],
    "refresh_quotes.py --wide": [{"UPSTOX_FUNDAMENTAL_ANALYTICS_TOKEN"}],
    "screener_login.py": [{"SCREENER_EMAIL", "SCREENER_PASSWORD"}],
}


def _workflows():
    return sorted(WF.glob("*.yml"))


def _exported(text: str) -> set[str]:
    """Env var names the workflow maps from secrets, at any level."""
    return set(re.findall(r"^\s*([A-Z_]+):\s*\$\{\{\s*secrets\.", text, re.M))


def _env_keys(text: str) -> set[str]:
    """Every env var the workflow SETS to a non-empty value, at any level.

    Parsed from the YAML rather than grepped, so a key that merely appears in a
    comment or a shell line does not count as provided.
    """
    import yaml

    found: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            env = node.get("env")
            if isinstance(env, dict):
                found.update(k for k, v in env.items()
                             if v is not None and str(v).strip() != "")
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(yaml.safe_load(text))
    return found


def test_each_workflow_exports_the_credentials_its_steps_need():
    problems = []
    for wf in _workflows():
        text = wf.read_text(encoding="utf-8")
        provided = _exported(text) | _env_keys(text)
        for script, alternatives in NEEDS.items():
            if script not in text:
                continue
            if not any(alt <= provided for alt in alternatives):
                wanted = " or ".join(sorted(str(sorted(a)) for a in alternatives))
                problems.append(f"{wf.name} runs {script} but provides none of {wanted}")
    assert not problems, "workflow(s) would silently no-op:\n  " + "\n  ".join(problems)


def test_the_guard_still_catches_a_workflow_with_no_provider_at_all():
    """The any-of relaxation must not turn this into a test that passes on
    anything. A debate step with neither a hosted key nor a local host is the
    original silent-no-op bug and must still fail."""
    text = (
        "name: x\n"
        "jobs:\n"
        "  j:\n"
        "    steps:\n"
        "      - run: python scripts/refresh_debate.py --top 10\n"
    )
    provided = _exported(text) | _env_keys(text)
    assert not any(alt <= provided for alt in NEEDS["refresh_debate.py"])

    ollama_ok = text.replace("    steps:\n", "    env:\n      OLLAMA_HOST: http://127.0.0.1:11434\n    steps:\n")
    provided = _exported(ollama_ok) | _env_keys(ollama_ok)
    assert any(alt <= provided for alt in NEEDS["refresh_debate.py"])


def test_an_empty_env_value_does_not_count_as_provided():
    """`OLLAMA_HOST: ""` resolves to empty at runtime and latches the adapter
    off exactly like an absent one."""
    text = (
        "name: x\n"
        "jobs:\n"
        "  j:\n"
        "    env:\n"
        '      OLLAMA_HOST: ""\n'
        "    steps:\n"
        "      - run: python scripts/refresh_debate.py\n"
    )
    assert "OLLAMA_HOST" not in _env_keys(text)


def test_secret_names_are_consistent_across_workflows():
    """The same credential must not be spelled two ways."""
    seen: dict[str, set[str]] = {}
    for wf in _workflows():
        for name in _exported(wf.read_text(encoding="utf-8")):
            seen.setdefault(name.upper(), set()).add(name)
    inconsistent = {k: v for k, v in seen.items() if len(v) > 1}
    assert not inconsistent, f"same secret spelled differently: {inconsistent}"


def test_every_exported_secret_maps_to_a_same_named_secret():
    """`FOO: ${{ secrets.BAR }}` is a rename waiting to become a silent outage."""
    odd = []
    for wf in _workflows():
        for env_name, secret_name in re.findall(
                r"^\s*([A-Z_]+):\s*\$\{\{\s*secrets\.([A-Z_]+)\s*\}\}", wf.read_text(encoding="utf-8"), re.M):
            if env_name != secret_name:
                odd.append(f"{wf.name}: env {env_name} <- secrets.{secret_name}")
    assert not odd, "env name differs from secret name:\n  " + "\n  ".join(odd)


# ------------------------------------------------ the ratios backfill must finish
def _refresh_boards_step():
    import yaml
    wf = yaml.safe_load((WF / "refresh-data.yml").read_text(encoding="utf-8"))
    steps = wf["jobs"]["refresh"]["steps"]
    return [s for s in steps if s.get("name") == "Refresh boards"][0]["run"]


def test_the_ratios_backfill_has_enough_budget_to_finish_the_universe():
    """Measured throughput is ~63 companies/min against 5,495 companies, so the
    old 20-minute budget could only ever reach the first ~1,261 — and because
    the skip test was "ratios_at == today", it restarted at index 0 every run
    and re-did that same prefix forever. Coverage sat at 32%, current_ratio at
    1%, with 4,223 bundles never touched."""
    body = _refresh_boards_step()
    m = re.search(r"--ratios-only[^\n]*--max-minutes (\d+)", body)
    assert m, "no ratios-only step found"
    assert int(m.group(1)) >= 45, "the backfill cannot reach the whole universe in this budget"


def test_the_backfill_skips_by_age_not_by_calendar_day():
    src = (ROOT / "scripts" / "refresh_fundamentals.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert "_ratios_fresh(" in code, "the freshness window is not applied"
    assert 'bundle.get("ratios_at") == today' not in code, \
        "same-day-only skip re-fetches everything and never advances"


def test_the_phase_one_budget_still_fits_the_job_timeout():
    """Raising one budget must not push the job past its own ceiling — a timeout
    there loses the whole phase, which is why the commit was split out."""
    import yaml
    wf = yaml.safe_load((WF / "refresh-data.yml").read_text(encoding="utf-8"))
    limit = int(wf["jobs"]["refresh"]["timeout-minutes"])
    spent = sum(int(m) for m in re.findall(r"--max-minutes (\d+)", _refresh_boards_step()))
    assert spent < limit - 40, f"phase-1 budget {spent}min leaves too little slack under {limit}min"
