"""Every refresh script must be run by something, or its board silently freezes.

refresh_portfolio.py, refresh_sector_medians.py and refresh_stdrl.py each
shipped with no workflow calling them. Nothing failed, nothing was logged, and
nothing looked wrong -- the boards simply stopped moving. Sector medians and
the portfolio book were 12 days stale before an audit noticed, STDRL 9.

That is the worst failure shape this repo has: a green pipeline producing a
page nobody updates. A missing entry here is cheap to add and impossible to
notice in production, so it is worth a test.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPTS = ROOT / "scripts"

#: Scripts that legitimately have no schedule, with the reason.
#:
#: These are run by hand or by another entry point. Adding a name here is a
#: deliberate statement that its output is not expected to stay fresh.
UNSCHEDULED = {
    # Repairs a specific historical defect; not a recurring job.
    "repair_blank_bundles.py": "one-off repair, kept for re-use",
    # Authenticates the session the other scripts reuse; called by them.
    "screener_login.py": "called by the workflow directly under its own step",
    # Emits a matrix for the debate workflow rather than writing a board.
    "debate_shards.py": "planner for debate-cloud, not a board writer",
    # Post-processing steps invoked by name inside other jobs.
    "redact_sources.py": "runs as its own workflow step",
    "ci_commit.sh": "shell helper",
}


def _workflow_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in WORKFLOWS.glob("*.yml"))


def _board_writers() -> list[str]:
    """Scripts named refresh_*.py -- the ones that write a published board."""
    return sorted(p.name for p in SCRIPTS.glob("refresh_*.py"))


def test_every_refresh_script_is_referenced_by_a_workflow():
    text = _workflow_text()
    orphans = [name for name in _board_writers()
               if name not in UNSCHEDULED and name not in text]
    assert not orphans, (
        "these write a board but nothing runs them, so it freezes silently:\n  "
        + "\n  ".join(orphans))


def test_the_guard_would_catch_a_new_orphan():
    """A test nobody has seen fail is not a guard."""
    text = _workflow_text()
    assert "refresh_this_does_not_exist.py" not in text
    assert "refresh_fundamentals.py" in text, "the check cannot see the workflows at all"


def test_the_exemptions_all_still_exist():
    """A stale exemption hides a script that was renamed or deleted."""
    missing = [n for n in UNSCHEDULED
               if not (SCRIPTS / n).exists()]
    assert not missing, f"exempted but absent: {missing}"


def test_stdrl_installs_what_it_needs_on_the_day_it_runs():
    """It is the only board needing torch, and the lean install does not carry
    it. Scheduling it without the deps would just fail every week."""
    text = _workflow_text()
    if "refresh_stdrl.py" not in text:
        return
    assert "stable-baselines3" in text, "stdrl scheduled without its dependency"
    assert re.search(r"date -u \+%u", text), \
        "stdrl is not day-gated; torch would install on every nightly run"
