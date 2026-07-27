"""
Provider-agnostic LLM contract — the ONE interface the pipeline talks to.

The Agentic-RAG spec's headline constraint is that scanX must never be tightly
coupled to a single LLM API: preprocessing, extraction, comparison and retrieval
stay deterministic, and switching providers must mean swapping ONE adapter, not
rewriting the workflow. This module is that contract and nothing else — no SDK
import, no network, no environment lookup, no global state — so it is safe to
import from anywhere: today's static GitHub-Pages batch bake and, unchanged,
tomorrow's FastAPI/Celery worker.

Usage:
    from earnings_intel.llm import LLMAdapter, LLMResponse, NoCredentials

    class EchoAdapter:                     # structural typing — no base class
        name = "echo"
        def complete(self, prompt, *, json_mode=True, temperature=0.0,
                     max_tokens=None) -> LLMResponse:
            return LLMResponse(text=prompt, model="echo-1",
                               provider=self.name, ok=True)

    isinstance(EchoAdapter(), LLMAdapter)          # -> True
    LLMResponse.failure("openai", "gpt-4o-mini", "HTTP 401").as_dict()
    # {'text': '', 'model': 'gpt-4o-mini', 'provider': 'openai', 'ok': False, ...}

Contract:
  * `complete()` NEVER raises for an EXPECTED condition (missing credentials,
    retired model, HTTP error, bad JSON). It returns LLMResponse(ok=False,
    error=...) so one dead key can never stop a bake. Callers that WANT the
    exception ask the adapter for `require_credentials()` first, which raises
    NoCredentials.
  * `text` is always a str (empty on failure); `provider` and `model` are always
    populated so a baked artefact records exactly who answered.
  * An adapter must NEVER put an API key into `error`, into a log line or into
    its repr.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Protocol, runtime_checkable

log = logging.getLogger("technofunda.llm")

__all__ = ["LLMResponse", "LLMAdapter", "LLMError", "NoCredentials"]


class LLMError(RuntimeError):
    """Any failure raised by the LLM layer (transport, protocol, config)."""


class NoCredentials(LLMError):
    """The adapter has no key/endpoint for its provider — it must be skipped."""


@dataclass
class LLMResponse:
    """One completion, or one recorded failure. Plain data: JSON-serialisable.

    ok=False responses are first-class — they are what lets `providers.complete`
    walk to the next credentialled provider instead of aborting the pipeline.
    """

    text: str
    model: str
    provider: str
    ok: bool
    error: str = ""

    @classmethod
    def failure(cls, provider: str, model: str, error: str) -> "LLMResponse":
        """A failed response — never carries text, always carries the reason."""
        return cls(text="", model=str(model or ""), provider=str(provider or ""),
                   ok=False, error=str(error or "")[:600])

    def as_dict(self) -> dict:
        """Artefact-ready dict (the bake emits plain JSON, not objects)."""
        return asdict(self)


@runtime_checkable
class LLMAdapter(Protocol):
    """What every provider adapter looks like from the pipeline's side.

    Structural: any object with `name` and a conforming `complete()` qualifies,
    so a test fake, a cached replayer or a future provider needs no inheritance.
    """

    name: str

    def complete(self, prompt: str, *, json_mode: bool = True,
                 temperature: float = 0.0,
                 max_tokens: int | None = None) -> LLMResponse:
        """Run one prompt. Deterministic by default (temperature 0, JSON out)."""
        ...
