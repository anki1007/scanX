"""
Provider-agnostic LLM layer — the model adapter is the ONLY provider-coupled
part of the Agentic-RAG pipeline.

Everything above this package (document discovery, extraction, grounding,
comparison, retrieval) stays deterministic and provider-free; switching from
Gemini to OpenAI/Anthropic/DeepSeek/a local Ollama model means changing a name,
not the workflow.

    from earnings_intel import llm

    llm.available()                                  # who has credentials
    r = llm.complete(prompt, provider="openai")      # falls back if that dies
    r.ok, r.provider, r.model, r.text, r.as_dict()

    ad = llm.get_adapter()                            # $SCANX_LLM_PROVIDER or gemini
    ad.complete(prompt, json_mode=True, temperature=0)

Contract lives in `base` (LLMResponse / LLMAdapter / LLMError / NoCredentials);
adapters and the registry live in `providers`.
"""
from __future__ import annotations

from .base import LLMAdapter, LLMError, LLMResponse, NoCredentials
from .providers import (
    PROVIDERS,
    AnthropicAdapter,
    BaseAdapter,
    DeepSeekAdapter,
    GeminiAdapter,
    OllamaAdapter,
    OpenAIAdapter,
    OpenAICompatibleAdapter,
    available,
    complete,
    get_adapter,
    register,
    resolve_name,
)

__all__ = [
    "LLMResponse", "LLMAdapter", "LLMError", "NoCredentials",
    "BaseAdapter", "GeminiAdapter", "OpenAIAdapter", "AnthropicAdapter",
    "DeepSeekAdapter", "OllamaAdapter", "OpenAICompatibleAdapter",
    "PROVIDERS", "register", "resolve_name", "get_adapter", "available",
    "complete",
]
