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

# script -> credentials it needs to do its real work (not merely to import)
NEEDS = {
    "refresh_fundamentals.py": {"UPSTOX_FUNDAMENTAL_ANALYTICS_TOKEN"},
    "refresh_docinsights.py": {"GEMINI_API_KEY"},
    # refresh_debate.py accepts ANY credentialled provider, but Gemini is the only
    # one this workflow exports — and with none set the script prints "no LLM
    # credentials configured" and exits 0. Green, silent, and no debates: the
    # precise failure shape this file exists to make impossible.
    "refresh_debate.py": {"GEMINI_API_KEY"},
    "refresh_quotes.py --wide": {"UPSTOX_FUNDAMENTAL_ANALYTICS_TOKEN"},
    "screener_login.py": {"SCREENER_EMAIL", "SCREENER_PASSWORD"},
}


def _workflows():
    return sorted(WF.glob("*.yml"))


def _exported(text: str) -> set[str]:
    """Env var names the workflow maps from secrets, at any level."""
    return set(re.findall(r"^\s*([A-Z_]+):\s*\$\{\{\s*secrets\.", text, re.M))


def test_each_workflow_exports_the_credentials_its_steps_need():
    problems = []
    for wf in _workflows():
        text = wf.read_text(encoding="utf-8")
        exported = _exported(text)
        for script, needed in NEEDS.items():
            if script not in text:
                continue
            missing = needed - exported
            if missing:
                problems.append(f"{wf.name} runs {script} but does not export {sorted(missing)}")
    assert not problems, "workflow(s) would silently no-op:\n  " + "\n  ".join(problems)


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
