"""Gemini model-chain resilience.

On 2026-07-27 every hardcoded model id had been retired or closed to new keys
(`gemini-2.5-flash` -> 404 NOT_FOUND, `gemini-2.0-flash` shut down), and because
404 was not treated as retryable the very first failure aborted the call. The
whole doc-analysis bake produced zero facts. These tests pin the fix.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data import insights_ai as ia  # noqa: E402

RETIRED = ("404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model "
           "models/gemini-2.5-flash is no longer available to new users.'}}")


class _Models:
    def __init__(self, ok_model, listing=()):
        self.ok_model, self.listing, self.tried = ok_model, listing, []

    def generate_content(self, *, model, contents, config):
        self.tried.append(model)
        if model != self.ok_model:
            raise RuntimeError(RETIRED)
        return type("R", (), {"text": '{"ok": true}'})()

    def list(self):
        return [type("M", (), {"name": "models/" + n,
                               "supported_actions": ["generateContent"]})()
                for n in self.listing]


class _Client:
    def __init__(self, ok_model, listing=()):
        self.models = _Models(ok_model, listing)


@pytest.fixture(autouse=True)
def _reset_discovery():
    ia._DISCOVERED.clear()
    yield
    ia._DISCOVERED.clear()


def _patch_client(monkeypatch, client):
    import types
    fake_genai = types.SimpleNamespace(Client=lambda api_key=None: client)
    monkeypatch.setitem(sys.modules, "google", types.SimpleNamespace(genai=fake_genai))
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)


def test_404_is_retryable_so_a_retired_model_falls_through():
    assert ia._retryable(RuntimeError(RETIRED))
    assert ia._retryable(RuntimeError("404 NOT_FOUND"))
    assert ia._retryable(RuntimeError("503 UNAVAILABLE"))
    assert ia._retryable(RuntimeError("429 RESOURCE_EXHAUSTED"))
    # a genuine bug must still surface immediately
    assert not ia._retryable(RuntimeError("400 INVALID_ARGUMENT: bad prompt"))


def test_chain_walks_past_retired_models_to_a_working_one(monkeypatch):
    client = _Client(ok_model="gemini-3.5-flash")
    _patch_client(monkeypatch, client)
    out = ia._gemini("p", "k", "gemini-2.5-flash")
    assert out == '{"ok": true}'
    assert client.models.tried[0] == "gemini-2.5-flash"      # tried the dead one first
    assert "gemini-3.5-flash" in client.models.tried         # and recovered


def test_default_model_is_not_a_retired_id():
    dead = {"gemini-2.0-flash", "gemini-2.0-flash-lite"}
    assert ia._DEFAULT_MODEL not in dead
    assert not (set(ia._FALLBACK_MODELS) & dead)


def test_discovery_rescues_the_call_when_every_hardcoded_id_is_dead(monkeypatch):
    client = _Client(ok_model="gemini-9.9-flash", listing=["gemini-9.9-flash"])
    _patch_client(monkeypatch, client)
    assert ia._gemini("p", "k", "gemini-2.5-flash") == '{"ok": true}'
    assert "gemini-9.9-flash" in client.models.tried


def test_busy_error_raised_when_nothing_works_at_all(monkeypatch):
    client = _Client(ok_model="never", listing=[])
    _patch_client(monkeypatch, client)
    with pytest.raises(ia.GeminiBusy):
        ia._gemini("p", "k", "gemini-2.5-flash")


def test_discovery_skips_non_generative_models(monkeypatch):
    client = _Client(ok_model="gemini-4-flash",
                     listing=["gemini-embedding-001", "gemini-4-flash-tts", "gemini-4-flash"])
    _patch_client(monkeypatch, client)
    ia._gemini("p", "k", "gemini-2.5-flash")
    assert "gemini-embedding-001" not in client.models.tried
    assert "gemini-4-flash-tts" not in client.models.tried
