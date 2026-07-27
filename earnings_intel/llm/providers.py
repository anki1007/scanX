"""
LLM provider adapters — one interface, many back ends, zero lock-in.

Every adapter implements `base.LLMAdapter` and nothing more: it lazily imports
its SDK (or just posts JSON with `requests`), reads ITS OWN environment
variable, and degrades to `NoCredentials` when that variable is absent. Adding a
provider is one dict entry in `PROVIDERS`; the workflow above never changes.

Nothing here holds global state: adapters are cheap instances, all IO goes
through `BaseAdapter._requests()` (injectable in tests), and the only
module-level mutable is the `PROVIDERS` registry itself — configuration, not
state. That is what lets the same code run inside today's static batch bake and,
unchanged, inside a FastAPI/Celery worker later.

Usage:
    from earnings_intel.llm import providers as llm

    llm.available()                       # ['gemini', 'anthropic'] — creds present
    r = llm.complete('Reply {"ok": true}')            # default provider + fallback
    r.ok, r.provider, r.model, r.text

    r = llm.complete(prompt, provider="anthropic", fallback=False)
    ad = llm.get_adapter("deepseek", model="deepseek-reasoner")
    ad.complete(prompt, json_mode=True, temperature=0)

    # add a provider in ONE line (Groq, vLLM, Together, any OpenAI-compatible URL)
    llm.register("groq", functools.partial(
        llm.OpenAICompatibleAdapter, "https://api.groq.com/openai/v1",
        "GROQ_API_KEY", name="groq", model="llama-3.3-70b-versatile"))

Environment:
    SCANX_LLM_PROVIDER   default provider name        (default "gemini")
    GEMINI_API_KEY       gemini      (also GOOGLE_API_KEY / local gemin_api_key.md)
    OPENAI_API_KEY       openai      OPENAI_MODEL
    ANTHROPIC_API_KEY    anthropic   ANTHROPIC_MODEL
    DEEPSEEK_API_KEY     deepseek    DEEPSEEK_MODEL
    MISTRAL_API_KEY / DASHSCOPE_API_KEY / TOGETHER_API_KEY   OpenAI-compatible
    OLLAMA_HOST          ollama — local, needs no key; setting it opts the local
                         runtime into the fallback chain (default endpoint
                         http://127.0.0.1:11434)

Keys are read at call time, used for that one call, and NEVER logged, never put
into LLMResponse.error and never shown in a repr — `_redact` scrubs both the
known credential and anything that merely looks like one out of every message.
"""
from __future__ import annotations

import functools
import logging
import os
import re
from typing import Callable, Optional

from .base import LLMAdapter, LLMError, LLMResponse, NoCredentials

log = logging.getLogger("technofunda.llm.providers")

__all__ = [
    "BaseAdapter", "GeminiAdapter", "OpenAIAdapter", "AnthropicAdapter",
    "DeepSeekAdapter", "OllamaAdapter", "OpenAICompatibleAdapter",
    "PROVIDERS", "DEFAULT_PROVIDER", "register", "resolve_name", "get_adapter",
    "available", "complete",
]

DEFAULT_PROVIDER = "gemini"
_ENV_PROVIDER = "SCANX_LLM_PROVIDER"
_TIMEOUT = 90
_MAX_ERR = 240
_JSON_HINT = "\n\nReturn one valid JSON value and nothing else."
_JSON_SYSTEM = "Reply with a single valid JSON value and nothing else. No prose, no code fences."

# Defence in depth: even if a provider echoes a credential back inside an error
# body, these shapes never reach a log line or a baked artefact.
_KEY_SHAPES = re.compile(
    r"(AIza[0-9A-Za-z_\-]{20,}|sk-ant-[0-9A-Za-z_\-]{16,}|sk-[0-9A-Za-z_\-]{16,})")


# --------------------------------------------------------------- pure helpers
def _env(name: str) -> Optional[str]:
    """Environment variable, stripped; None when unset or blank."""
    v = os.environ.get(str(name or ""), "")
    v = (v or "").strip()
    return v or None


def _redact(text, *secrets) -> str:
    """Scrub known credentials AND key-shaped substrings out of a message."""
    s = str(text or "")
    for sec in secrets:
        sec = str(sec or "").strip()
        if len(sec) >= 8:                       # too short to be a real key
            s = s.replace(sec, "***")
    return _KEY_SHAPES.sub("***", s)


def _with_json_hint(prompt: str) -> str:
    """OpenAI-style json_mode 400s unless the word "json" is in the prompt."""
    p = str(prompt or "")
    return p if "json" in p.lower() else p + _JSON_HINT


def _first_choice(data: dict, provider: str) -> str:
    """Text out of an OpenAI-shaped chat/completions response."""
    try:
        choices = (data or {}).get("choices") or []
        msg = (choices[0] or {}).get("message") or {}
        txt = msg.get("content")
        if isinstance(txt, list):               # some servers return parts
            txt = "".join(str(p.get("text") or "") for p in txt if isinstance(p, dict))
        return str(txt or "")
    except Exception:  # noqa: BLE001
        raise LLMError(f"{provider}: unexpected chat-completions response shape") from None


def _anthropic_text(data: dict) -> str:
    """Text out of an Anthropic /v1/messages response (all text blocks)."""
    blocks = (data or {}).get("content") or []
    if not isinstance(blocks, list):
        raise LLMError("anthropic: unexpected messages response shape")
    return "".join(str(b.get("text") or "") for b in blocks
                   if isinstance(b, dict) and b.get("type", "text") == "text")


# ------------------------------------------------------------- adapter plumbing
class BaseAdapter:
    """Shared plumbing: credential lookup, redaction, uniform LLMResponse.

    Subclasses implement `_invoke()` and may override `credentials_present()` /
    `require_credentials()`. `complete()` is deliberately final-ish: it is the
    single place that turns any exception into an ok=False response, so no
    provider can ever raise into the pipeline.
    """

    name = "base"
    env_var = ""
    model_env = ""
    default_model = ""

    def __init__(self, *, model: Optional[str] = None, api_key: Optional[str] = None,
                 timeout: int = _TIMEOUT) -> None:
        self._api_key = (str(api_key).strip() if api_key else None)
        self.model = str(model or _env(self.model_env) or self.default_model or "")
        self.timeout = int(timeout or _TIMEOUT)

    def __repr__(self) -> str:                  # never the key
        return f"<{type(self).__name__} name={self.name!r} model={self.model!r}>"

    # ------------------------------------------------------------ credentials
    def credentials_present(self) -> bool:
        """True when this adapter could run right now. Never touches network."""
        return bool(self._api_key or _env(self.env_var))

    def require_credentials(self) -> str:
        """The credential for one call. Raises NoCredentials when absent."""
        key = self._api_key or _env(self.env_var)
        if not key:
            raise NoCredentials(
                f"{self.name}: no credentials — set ${self.env_var or 'API_KEY'}")
        return key

    # -------------------------------------------------------------------- IO
    def _requests(self):
        """The HTTP client. One seam — tests inject a fake, no network."""
        try:
            import requests                     # lazy: optional at import time
        except Exception:  # noqa: BLE001
            raise LLMError(f"{self.name}: requests is not installed") from None
        return requests

    def _post(self, url: str, *, headers: dict, payload: dict,
              credential: str = "") -> dict:
        r = self._requests().post(url, headers=headers, json=payload,
                                  timeout=self.timeout)
        code = int(getattr(r, "status_code", 0) or 0)
        if code != 200:
            body = _redact(getattr(r, "text", "") or "", credential)[:200]
            raise LLMError(f"{self.name} HTTP {code}: {body}")
        try:
            data = r.json()
        except Exception:  # noqa: BLE001
            raise LLMError(f"{self.name}: response was not JSON") from None
        if not isinstance(data, dict):
            raise LLMError(f"{self.name}: response was not a JSON object")
        return data

    # --------------------------------------------------------------- contract
    def _invoke(self, prompt: str, *, credential: str, json_mode: bool,
                temperature: float, max_tokens: Optional[int]) -> str:
        raise NotImplementedError

    def complete(self, prompt: str, *, json_mode: bool = True,
                 temperature: float = 0.0,
                 max_tokens: int | None = None) -> LLMResponse:
        """One prompt -> one LLMResponse. Never raises; failures come back ok=False."""
        try:
            credential = self.require_credentials()
        except NoCredentials as e:
            log.debug("%s skipped: %s", self.name, e)
            return LLMResponse.failure(self.name, self.model, f"NoCredentials: {e}")
        try:
            text = self._invoke(str(prompt or ""), credential=credential,
                                json_mode=bool(json_mode),
                                temperature=float(temperature or 0.0),
                                max_tokens=(int(max_tokens) if max_tokens else None))
        except Exception as e:  # noqa: BLE001
            err = _redact(f"{type(e).__name__}: {e}", credential)[:_MAX_ERR]
            log.warning("%s call failed: %s", self.name, err)
            return LLMResponse.failure(self.name, self.model, err)
        return LLMResponse(text=str(text or ""), model=self.model,
                           provider=self.name, ok=True)


class _ChatCompletionsAdapter(BaseAdapter):
    """Any server speaking OpenAI's POST {base_url}/chat/completions."""

    base_url = ""
    max_tokens_field = "max_tokens"
    supports_json_mode = True

    def _headers(self, credential: str) -> dict:
        return {"Authorization": f"Bearer {credential}",
                "Content-Type": "application/json"}

    def _invoke(self, prompt: str, *, credential: str, json_mode: bool,
                temperature: float, max_tokens: Optional[int]) -> str:
        if json_mode:
            prompt = _with_json_hint(prompt)
        payload: dict = {"temperature": temperature,
                         "messages": [{"role": "user", "content": prompt}]}
        if self.model:                          # single-model servers ignore it
            payload["model"] = self.model
        if json_mode and self.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}
        if max_tokens:
            payload[self.max_tokens_field] = int(max_tokens)
        data = self._post(self.base_url.rstrip("/") + "/chat/completions",
                          headers=self._headers(credential), payload=payload,
                          credential=credential)
        return _first_choice(data, self.name)


# ------------------------------------------------------------------- providers
class GeminiAdapter(BaseAdapter):
    """Google Gemini — DELEGATES to insights_ai._gemini.

    That function already owns the model-fallback chain and the self-healing
    model discovery that keeps the bake alive when Google retires an id; it is
    reused here rather than reimplemented. `max_tokens` is not part of that
    entry point and is ignored.
    """

    name = "gemini"
    env_var = "GEMINI_API_KEY"
    model_env = "GEMINI_MODEL"

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        if not self.model:
            try:
                self.model = self._ia()._DEFAULT_MODEL
            except Exception:  # noqa: BLE001
                self.model = "gemini-flash"

    @staticmethod
    def _ia():
        from ..data import insights_ai as ia    # lazy: google-genai stays optional
        return ia

    def credentials_present(self) -> bool:
        if self._api_key or _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY"):
            return True
        try:
            return bool(self._ia().have_key())  # also the local gemin_api_key.md
        except Exception:  # noqa: BLE001
            return False

    def require_credentials(self) -> str:
        key = self._api_key or _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
        if not key:
            try:
                key = self._ia()._api_key()
            except Exception:  # noqa: BLE001
                key = None
        if not key:
            raise NoCredentials("gemini: no credentials — set $GEMINI_API_KEY "
                                "(or keep a local gemin_api_key.md)")
        return key

    def _invoke(self, prompt: str, *, credential: str, json_mode: bool,
                temperature: float, max_tokens: Optional[int]) -> str:
        return self._ia()._gemini(prompt, credential, self.model,
                                  json_mode=json_mode, temperature=temperature)


class OpenAIAdapter(_ChatCompletionsAdapter):
    """OpenAI api.openai.com/v1/chat/completions."""

    name = "openai"
    env_var = "OPENAI_API_KEY"
    model_env = "OPENAI_MODEL"
    default_model = "gpt-4o-mini"
    base_url = "https://api.openai.com/v1"
    max_tokens_field = "max_completion_tokens"  # the current OpenAI spelling


class AnthropicAdapter(BaseAdapter):
    """Anthropic api.anthropic.com/v1/messages."""

    name = "anthropic"
    env_var = "ANTHROPIC_API_KEY"
    model_env = "ANTHROPIC_MODEL"
    default_model = "claude-sonnet-4-6"
    base_url = "https://api.anthropic.com/v1"
    api_version = "2023-06-01"
    default_max_tokens = 4096

    def _invoke(self, prompt: str, *, credential: str, json_mode: bool,
                temperature: float, max_tokens: Optional[int]) -> str:
        payload: dict = {
            "model": self.model,
            "max_tokens": int(max_tokens or self.default_max_tokens),
            "temperature": temperature,
            "messages": [{"role": "user", "content": str(prompt or "")}],
        }
        if json_mode:                           # no response_format on this API
            payload["system"] = _JSON_SYSTEM
        data = self._post(self.base_url.rstrip("/") + "/messages",
                          headers={"x-api-key": credential,
                                   "anthropic-version": self.api_version,
                                   "content-type": "application/json"},
                          payload=payload, credential=credential)
        return _anthropic_text(data)


class DeepSeekAdapter(_ChatCompletionsAdapter):
    """DeepSeek — OpenAI-compatible base url."""

    name = "deepseek"
    env_var = "DEEPSEEK_API_KEY"
    model_env = "DEEPSEEK_MODEL"
    default_model = "deepseek-chat"
    base_url = "https://api.deepseek.com/v1"


class OllamaAdapter(BaseAdapter):
    """Local Ollama runtime — no key, POST {host}/api/generate.

    `OLLAMA_HOST` is the opt-in switch: without it the adapter reports no
    credentials so a batch bake never stalls on a localhost port that isn't
    listening. Set it (even to the default) to put local models in the chain.
    """

    name = "ollama"
    env_var = "OLLAMA_HOST"
    model_env = "OLLAMA_MODEL"
    default_model = "llama3.1"
    default_host = "http://127.0.0.1:11434"

    def __init__(self, *, host: Optional[str] = None, **kw) -> None:
        super().__init__(**kw)
        self._host = (str(host).strip() if host else None)

    @property
    def host(self) -> str:
        return (self._host or _env("OLLAMA_HOST") or self.default_host).rstrip("/")

    def credentials_present(self) -> bool:
        return bool(self._host or _env("OLLAMA_HOST"))

    def require_credentials(self) -> str:
        """Returns the endpoint (no key exists) or raises NoCredentials."""
        if not self.credentials_present():
            raise NoCredentials("ollama: local runtime not opted in — set "
                                f"$OLLAMA_HOST (e.g. {self.default_host})")
        return self.host

    def _invoke(self, prompt: str, *, credential: str, json_mode: bool,
                temperature: float, max_tokens: Optional[int]) -> str:
        options: dict = {"temperature": temperature}
        if max_tokens:
            options["num_predict"] = int(max_tokens)
        payload: dict = {"model": self.model, "prompt": str(prompt or ""),
                         "stream": False, "options": options}
        if json_mode:
            payload["format"] = "json"
        data = self._post(self.host + "/api/generate",
                          headers={"Content-Type": "application/json"},
                          payload=payload)          # no credential to redact
        return str(data.get("response") or "")


class OpenAICompatibleAdapter(_ChatCompletionsAdapter):
    """Any OpenAI-compatible endpoint — Qwen/DashScope, Mistral, Together, vLLM…

        OpenAICompatibleAdapter("http://localhost:8000/v1", "VLLM_API_KEY",
                                name="vllm", model="Qwen2.5-32B-Instruct")
    """

    name = "openai-compatible"

    def __init__(self, base_url: str, env_var: str, *, name: Optional[str] = None,
                 model: Optional[str] = None, model_env: str = "",
                 supports_json_mode: bool = True, **kw) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.env_var = str(env_var or "")
        self.model_env = str(model_env or "")
        self.supports_json_mode = bool(supports_json_mode)
        if name:
            self.name = str(name)
        super().__init__(model=model, **kw)
        if not self.base_url:
            raise LLMError("OpenAICompatibleAdapter needs a base_url")


def _compatible(base_url: str, env_var: str, name: str, model: str,
                model_env: str = "") -> Callable[..., LLMAdapter]:
    return functools.partial(OpenAICompatibleAdapter, base_url, env_var,
                             name=name, model=model, model_env=model_env)


# --------------------------------------------------------------- the registry
# Insertion order IS the deterministic fallback order: the default provider
# first, local last. Adding a provider is one entry — nothing else changes.
PROVIDERS: dict[str, Callable[..., LLMAdapter]] = {
    "gemini": GeminiAdapter,
    "openai": OpenAIAdapter,
    "anthropic": AnthropicAdapter,
    "deepseek": DeepSeekAdapter,
    "mistral": _compatible("https://api.mistral.ai/v1", "MISTRAL_API_KEY",
                           "mistral", "mistral-large-latest", "MISTRAL_MODEL"),
    "qwen": _compatible("https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                        "DASHSCOPE_API_KEY", "qwen", "qwen-plus", "QWEN_MODEL"),
    "together": _compatible("https://api.together.xyz/v1", "TOGETHER_API_KEY",
                            "together", "meta-llama/Llama-3.3-70B-Instruct-Turbo",
                            "TOGETHER_MODEL"),
    "ollama": OllamaAdapter,
}

_ALIASES = {"google": "gemini", "google-gemini": "gemini", "gpt": "openai",
            "chatgpt": "openai", "claude": "anthropic", "local": "ollama",
            "llama": "ollama", "dashscope": "qwen"}


def register(name: str, factory: Callable[..., LLMAdapter]) -> None:
    """Add or replace a provider. `PROVIDERS[name] = factory` works too."""
    key = str(name or "").strip().lower()
    if not key or not callable(factory):
        raise LLMError("register(name, factory) needs a name and a callable")
    PROVIDERS[key] = factory


def resolve_name(name: Optional[str] = None) -> str:
    """Provider name -> registry key. Explicit arg > $SCANX_LLM_PROVIDER > default."""
    raw = name or _env(_ENV_PROVIDER) or DEFAULT_PROVIDER
    key = str(raw).strip().lower()
    key = _ALIASES.get(key, key)
    if key not in PROVIDERS:
        raise LLMError(f"unknown LLM provider {str(raw)!r} — known: "
                       + ", ".join(sorted(PROVIDERS)))
    return key


def get_adapter(name: Optional[str] = None, **kwargs) -> LLMAdapter:
    """Build the named adapter (or $SCANX_LLM_PROVIDER's, else gemini).

    Extra kwargs go to the factory: get_adapter("openai", model="gpt-4o").
    Raises LLMError for an unknown name — a typo must never silently fall back.
    """
    return PROVIDERS[resolve_name(name)](**kwargs)


def _has_credentials(adapter) -> bool:
    """True unless the adapter says otherwise (third-party adapters may not say)."""
    fn = getattr(adapter, "credentials_present", None)
    if not callable(fn):
        return True
    try:
        return bool(fn())
    except Exception:  # noqa: BLE001
        return False


def available() -> list[str]:
    """Registered providers whose credentials are present, in registry order."""
    out: list[str] = []
    for nm, factory in PROVIDERS.items():
        try:
            if _has_credentials(factory()):
                out.append(nm)
        except Exception as e:  # noqa: BLE001
            log.debug("provider %s could not be built: %s", nm, _redact(e))
    return out


def complete(prompt: str, *, provider: Optional[str] = None,
             fallback: bool = True, **kw) -> LLMResponse:
    """Run one prompt on the chosen provider, then on every OTHER credentialled
    provider until one answers.

    A dead key, an exhausted quota or a retired model therefore never stops the
    pipeline. The order is deterministic (chosen provider first, then registry
    order) and every hand-off is logged. `fallback=False` tries only the chosen
    provider. Extra kwargs (json_mode, temperature, max_tokens) are forwarded to
    the adapter. Never raises except for an unknown provider name.
    """
    chosen = resolve_name(provider)
    order = [chosen] + ([n for n in PROVIDERS if n != chosen] if fallback else [])
    log.debug("LLM order: %s", ", ".join(order))

    problems: list[str] = []
    for nm in order:
        factory = PROVIDERS.get(nm)
        if factory is None:                      # registry mutated mid-flight
            continue
        try:
            adapter = factory()
        except Exception as e:  # noqa: BLE001
            problems.append(f"{nm}: {type(e).__name__}")
            continue
        if nm != chosen and not _has_credentials(adapter):
            log.debug("LLM fallback: skipping %s (no credentials)", nm)
            continue
        try:
            resp = adapter.complete(prompt, **kw)
        except Exception as e:  # noqa: BLE001
            resp = LLMResponse.failure(nm, "", _redact(f"{type(e).__name__}: {e}")[:_MAX_ERR])
        if getattr(resp, "ok", False):
            if nm != chosen:
                log.warning("LLM fallback: %s unusable — answered by %s", chosen, nm)
            return resp
        problems.append(f"{nm}: {_redact(getattr(resp, 'error', ''))}")
        log.warning("LLM provider %s failed: %s", nm, _redact(getattr(resp, "error", "")))

    return LLMResponse.failure(
        chosen, "", "; ".join(problems)[:600] or "no LLM provider configured")
