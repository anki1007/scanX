"""The completion timeout has to fit the machine doing the completing.

90 seconds is right for a hosted API and far too short for a local model on
CPU. Measured on an Actions runner, qwen2.5:3b takes 90-160s for one debate
turn, so the call timed out, the turn was dropped, and companies were written
with "2 turns over 1 rounds" instead of 4 over 2.

That is the failure worth guarding: it did not error, it did not skip the
company, it published half a debate and reported success.
"""
import importlib
import os
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import earnings_intel.llm.providers as providers  # noqa: E402


@pytest.fixture
def reload_with(monkeypatch):
    def _reload(value):
        if value is None:
            monkeypatch.delenv("SCANX_LLM_TIMEOUT", raising=False)
        else:
            monkeypatch.setenv("SCANX_LLM_TIMEOUT", value)
        return importlib.reload(providers)
    yield _reload
    monkeypatch.delenv("SCANX_LLM_TIMEOUT", raising=False)
    importlib.reload(providers)


def test_hosted_default_is_unchanged(reload_with):
    assert reload_with(None)._TIMEOUT == 90


def test_a_slow_local_runner_can_ask_for_more(reload_with):
    assert reload_with("420")._TIMEOUT == 420


@pytest.mark.parametrize("junk", ["", "junk", "-5", "0", "nan"])
def test_junk_falls_back_rather_than_disabling_the_timeout(reload_with, junk):
    """A zero or negative timeout would mean 'wait forever', which turns one
    wedged call into a six-hour job that bakes nothing."""
    assert reload_with(junk)._TIMEOUT == 90


def test_the_cloud_workflow_actually_sets_it():
    """The knob is worthless if the one runner that needs it does not use it."""
    wf = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "debate-cloud.yml").read_text(encoding="utf-8"))
    steps = wf["jobs"]["bake"]["steps"]
    bake = [s for s in steps if str(s.get("name", "")).startswith("Bake shard")]
    assert bake, "no bake step found"
    env = bake[0].get("env") or {}
    assert "SCANX_LLM_TIMEOUT" in env, "the CPU runner still uses the hosted default"
    assert int(env["SCANX_LLM_TIMEOUT"]) >= 300, \
        "too short for CPU inference; turns will be dropped silently"


def test_the_bake_budget_fits_inside_the_job_timeout():
    """A shard killed mid-write loses its slice. The bake budget plus setup
    must stay under the job ceiling."""
    text = (ROOT / ".github" / "workflows" / "debate-cloud.yml").read_text(encoding="utf-8")
    wf = yaml.safe_load(text)
    ceiling = int(wf["jobs"]["bake"]["timeout-minutes"])
    assert ceiling <= 360, "above the GitHub-hosted job limit"
    default_budget = int(re.search(r"BAKE_MINUTES:.*?'(\d+)'", text).group(1))
    assert default_budget <= ceiling - 60, \
        "no room for checkout, model restore and artifact upload"
