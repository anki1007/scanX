"""Provider-agnostic LLM layer.

The spec's "Important Requirement" is that scanX is never welded to one LLM API:
the workflow talks to `LLMAdapter`, and swapping Gemini for OpenAI / Anthropic /
DeepSeek / a local Ollama is a registry entry, not a rewrite. These tests pin
the contract, the credential gating, the deterministic fallback chain and the
rule that no adapter may ever return or log a key.

Nothing here touches the network or needs an API key: providers are fakes, the
HTTP client is injected through `BaseAdapter._requests`, and every environment
variable is monkeypatched.
"""
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from earnings_intel.data import insights_ai as ia  # noqa: E402
from earnings_intel.llm import base  # noqa: E402
from earnings_intel.llm import providers as pv  # noqa: E402

SENTINEL = "sk-TESTSENTINELdoNOTleak0000000000"
GEMINI_SENTINEL = "AIzaSyTESTKEYSENTINELdoNOTleak0000000"

_KEY_ENVS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
             "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY",
             "DASHSCOPE_API_KEY", "TOGETHER_API_KEY", "OLLAMA_HOST",
             "SCANX_LLM_PROVIDER", "OPENAI_MODEL", "ANTHROPIC_MODEL",
             "DEEPSEEK_MODEL", "GEMINI_MODEL", "OLLAMA_MODEL")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """No inherited credentials, and no local gemin_api_key.md leaking in."""
    for ev in _KEY_ENVS:
        monkeypatch.delenv(ev, raising=False)
    monkeypatch.setattr(ia, "_api_key", lambda: None)
    monkeypatch.setattr(ia, "have_key", lambda: False)
    yield


# --------------------------------------------------------------------- fakes
class FakeAdapter:
    """A conforming adapter with no base class — structural typing only."""

    def __init__(self, name="fake", *, creds=True, ok=True, text='{"ok": true}',
                 error="provider exploded", attempts=None):
        self.name = name
        self.model = f"{name}-1"
        self._creds = creds
        self._ok = ok
        self._text = text
        self._error = error
        self.calls = []
        self._attempts = attempts if attempts is not None else []

    def credentials_present(self):
        return self._creds

    def complete(self, prompt, *, json_mode=True, temperature=0.0, max_tokens=None):
        self.calls.append({"prompt": prompt, "json_mode": json_mode,
                           "temperature": temperature, "max_tokens": max_tokens})
        self._attempts.append(self.name)
        if not self._ok:
            return base.LLMResponse.failure(self.name, self.model, self._error)
        return base.LLMResponse(text=self._text, model=self.model,
                                provider=self.name, ok=True)


def _registry(monkeypatch, **adapters):
    """Replace PROVIDERS with zero-arg factories over the given fakes (ordered)."""
    monkeypatch.setattr(pv, "PROVIDERS",
                        {nm: (lambda a=ad: a) for nm, ad in adapters.items()})
    return adapters


class _FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class _FakeRequests:
    """Stands in for the `requests` module — records the call, returns canned."""

    def __init__(self, response=None):
        self.response = response or _FakeResponse()
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {},
                           "payload": json or {}, "timeout": timeout})
        return self.response


def _http(monkeypatch, response=None):
    fake = _FakeRequests(response)
    monkeypatch.setattr(pv.BaseAdapter, "_requests", lambda self: fake)
    return fake


def _chat_ok(content='{"ok": true}'):
    return _FakeResponse(payload={"choices": [{"message": {"content": content}}]})


# ------------------------------------------------------------------ contract
def test_response_shape_and_defaults():
    r = base.LLMResponse(text="hi", model="m1", provider="p1", ok=True)
    assert (r.text, r.model, r.provider, r.ok, r.error) == ("hi", "m1", "p1", True, "")
    assert r.as_dict() == {"text": "hi", "model": "m1", "provider": "p1",
                           "ok": True, "error": ""}

    f = base.LLMResponse.failure("p1", "m1", "HTTP 401")
    assert f.ok is False and f.text == "" and f.error == "HTTP 401"
    assert f.provider == "p1" and f.model == "m1"


def test_error_hierarchy():
    assert issubclass(base.NoCredentials, base.LLMError)
    assert issubclass(base.LLMError, RuntimeError)


def test_every_builtin_adapter_satisfies_the_protocol():
    for name in pv.PROVIDERS:
        adapter = pv.get_adapter(name)
        assert isinstance(adapter, base.LLMAdapter), name
        assert isinstance(adapter.name, str) and adapter.name
    assert isinstance(FakeAdapter(), base.LLMAdapter)   # no inheritance needed


# ------------------------------------------------------------------ registry
def test_registry_lookup_returns_the_right_adapter():
    assert isinstance(pv.get_adapter("gemini"), pv.GeminiAdapter)
    assert isinstance(pv.get_adapter("openai"), pv.OpenAIAdapter)
    assert isinstance(pv.get_adapter("anthropic"), pv.AnthropicAdapter)
    assert isinstance(pv.get_adapter("deepseek"), pv.DeepSeekAdapter)
    assert isinstance(pv.get_adapter("ollama"), pv.OllamaAdapter)
    assert isinstance(pv.get_adapter("qwen"), pv.OpenAICompatibleAdapter)


def test_unknown_provider_raises():
    with pytest.raises(base.LLMError):
        pv.get_adapter("not-a-provider")
    with pytest.raises(base.LLMError):
        pv.resolve_name("gemeni")               # a typo must never silently work


def test_default_provider_is_gemini_and_env_overrides_it(monkeypatch):
    assert pv.resolve_name() == "gemini"
    assert pv.get_adapter().name == "gemini"

    monkeypatch.setenv("SCANX_LLM_PROVIDER", "anthropic")
    assert pv.resolve_name() == "anthropic"
    assert pv.get_adapter().name == "anthropic"
    assert pv.get_adapter("openai").name == "openai"   # explicit beats env


def test_env_selection_is_case_and_alias_tolerant(monkeypatch):
    monkeypatch.setenv("SCANX_LLM_PROVIDER", "  Claude ")
    assert pv.resolve_name() == "anthropic"
    assert pv.resolve_name("GPT") == "openai"
    assert pv.resolve_name("google") == "gemini"


def test_adding_a_provider_is_one_registry_entry(monkeypatch):
    monkeypatch.setattr(pv, "PROVIDERS", dict(pv.PROVIDERS))
    pv.register("myvllm", lambda **kw: pv.OpenAICompatibleAdapter(
        "http://localhost:8000/v1", "VLLM_API_KEY", name="myvllm", **kw))
    ad = pv.get_adapter("myvllm")
    assert ad.name == "myvllm" and ad.base_url == "http://localhost:8000/v1"
    with pytest.raises(base.LLMError):
        pv.register("", None)


def test_adapter_kwargs_reach_the_factory():
    ad = pv.get_adapter("openai", model="gpt-4o", api_key="x" * 20)
    assert ad.model == "gpt-4o" and ad.credentials_present()


def test_model_comes_from_the_providers_own_env_var(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-reasoner")
    assert pv.get_adapter("openai").model == "gpt-4o"
    assert pv.get_adapter("deepseek").model == "deepseek-reasoner"
    assert pv.get_adapter("anthropic").model == pv.AnthropicAdapter.default_model


# --------------------------------------------------------------- credentials
def test_missing_key_gives_no_credentials(monkeypatch):
    ad = pv.OpenAIAdapter()
    assert ad.credentials_present() is False
    with pytest.raises(base.NoCredentials):
        ad.require_credentials()

    r = ad.complete("hello")                    # degrades, never raises
    assert r.ok is False and r.text == ""
    assert r.provider == "openai" and "NoCredentials" in r.error
    assert "OPENAI_API_KEY" in r.error          # tells the operator what to set


def test_each_adapter_reads_only_its_own_env_var(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    assert pv.AnthropicAdapter().credentials_present() is True
    assert pv.OpenAIAdapter().credentials_present() is False
    assert pv.DeepSeekAdapter().credentials_present() is False
    assert pv.GeminiAdapter().credentials_present() is False
    assert pv.available() == ["anthropic"]

    monkeypatch.setenv("DEEPSEEK_API_KEY", SENTINEL)
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_SENTINEL)
    assert pv.available() == ["gemini", "anthropic", "deepseek"]   # registry order


def test_gemini_also_accepts_google_api_key_and_the_local_file(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", GEMINI_SENTINEL)
    assert pv.GeminiAdapter().credentials_present() is True

    monkeypatch.delenv("GOOGLE_API_KEY")
    monkeypatch.setattr(ia, "have_key", lambda: True)
    monkeypatch.setattr(ia, "_api_key", lambda: GEMINI_SENTINEL)
    assert pv.GeminiAdapter().credentials_present() is True
    assert pv.GeminiAdapter().require_credentials() == GEMINI_SENTINEL


def test_ollama_needs_no_key_but_must_be_opted_in(monkeypatch):
    ad = pv.OllamaAdapter()
    assert ad.credentials_present() is False    # localhost is never assumed
    with pytest.raises(base.NoCredentials):
        ad.require_credentials()

    monkeypatch.setenv("OLLAMA_HOST", "http://box:11434/")
    ad = pv.OllamaAdapter()
    assert ad.credentials_present() is True
    assert ad.require_credentials() == "http://box:11434"


# ------------------------------------------------------------- adapter calls
def test_gemini_delegates_to_insights_ai_and_does_not_reimplement_fallback(monkeypatch):
    seen = {}

    def fake_gemini(prompt, key, model, json_mode=True, temperature=0):
        seen.update(prompt=prompt, key=key, model=model,
                    json_mode=json_mode, temperature=temperature)
        return '{"facts": []}'

    monkeypatch.setattr(ia, "_gemini", fake_gemini)
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_SENTINEL)

    r = pv.GeminiAdapter(model="gemini-x").complete("extract", json_mode=True,
                                                    temperature=0.0)
    assert r.ok and r.text == '{"facts": []}'
    assert r.provider == "gemini" and r.model == "gemini-x"
    assert seen == {"prompt": "extract", "key": GEMINI_SENTINEL,
                    "model": "gemini-x", "json_mode": True, "temperature": 0.0}
    # the model chain / self-healing discovery lives in insights_ai — reused, not copied
    src = (ROOT / "earnings_intel" / "llm" / "providers.py").read_text(encoding="utf-8")
    for owned_by_insights_ai in ("_FALLBACK_MODELS", "_discover_flash_model",
                                 "generate_content", "google.genai"):
        assert owned_by_insights_ai not in src


def test_gemini_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_SENTINEL)
    monkeypatch.setattr(ia, "_gemini", lambda *a, **k: (_ for _ in ()).throw(
        ia.GeminiBusy("all Gemini models busy")))
    r = pv.GeminiAdapter().complete("p")
    assert r.ok is False and "GeminiBusy" in r.error and r.provider == "gemini"


def test_openai_posts_chat_completions_with_bearer_auth(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SENTINEL)
    http = _http(monkeypatch, _chat_ok('{"a": 1}'))

    r = pv.OpenAIAdapter().complete("give me json", json_mode=True,
                                    temperature=0.0, max_tokens=256)
    assert r.ok and r.text == '{"a": 1}' and r.provider == "openai"

    call = http.calls[0]
    assert call["url"] == "https://api.openai.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == f"Bearer {SENTINEL}"
    assert call["payload"]["model"] == "gpt-4o-mini"
    assert call["payload"]["temperature"] == 0.0
    assert call["payload"]["response_format"] == {"type": "json_object"}
    assert call["payload"]["max_completion_tokens"] == 256
    assert call["payload"]["messages"] == [{"role": "user", "content": "give me json"}]


def test_json_mode_prompt_always_mentions_json(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SENTINEL)
    http = _http(monkeypatch, _chat_ok())
    pv.OpenAIAdapter().complete("summarise this filing", json_mode=True)
    sent = http.calls[0]["payload"]["messages"][0]["content"]
    assert "json" in sent.lower() and sent.startswith("summarise this filing")

    http = _http(monkeypatch, _chat_ok())
    pv.OpenAIAdapter().complete("summarise this filing", json_mode=False)
    assert http.calls[0]["payload"]["messages"][0]["content"] == "summarise this filing"
    assert "response_format" not in http.calls[0]["payload"]


def test_anthropic_posts_messages_with_api_key_header(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    http = _http(monkeypatch, _FakeResponse(payload={
        "content": [{"type": "text", "text": '{"a": '}, {"type": "text", "text": "1}"}]}))

    r = pv.AnthropicAdapter(model="claude-x").complete("p", json_mode=True)
    assert r.ok and r.text == '{"a": 1}' and r.model == "claude-x"

    call = http.calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"]["x-api-key"] == SENTINEL
    assert call["headers"]["anthropic-version"] == "2023-06-01"
    assert call["payload"]["max_tokens"] >= 1          # required by that API
    assert "json" in call["payload"]["system"].lower()


def test_deepseek_and_openai_compatible_use_their_own_base_url(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", SENTINEL)
    http = _http(monkeypatch, _chat_ok())
    pv.DeepSeekAdapter().complete("p")
    assert http.calls[0]["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert http.calls[0]["payload"]["model"] == "deepseek-chat"

    monkeypatch.setenv("VLLM_KEY", SENTINEL)
    http = _http(monkeypatch, _chat_ok())
    ad = pv.OpenAICompatibleAdapter("http://gpu-box:8000/v1/", "VLLM_KEY",
                                    name="vllm", model="Qwen2.5-32B")
    assert ad.credentials_present() is True
    ad.complete("p")
    assert http.calls[0]["url"] == "http://gpu-box:8000/v1/chat/completions"
    assert http.calls[0]["payload"]["model"] == "Qwen2.5-32B"


def test_ollama_generate_needs_no_auth_header(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    http = _http(monkeypatch, _FakeResponse(payload={"response": '{"ok": true}'}))

    r = pv.OllamaAdapter(model="qwen2.5").complete("p", json_mode=True, max_tokens=64)
    assert r.ok and r.text == '{"ok": true}' and r.provider == "ollama"

    call = http.calls[0]
    assert call["url"] == "http://127.0.0.1:11434/api/generate"
    assert "Authorization" not in call["headers"] and "x-api-key" not in call["headers"]
    assert call["payload"]["format"] == "json"
    assert call["payload"]["options"]["num_predict"] == 64
    assert call["payload"]["stream"] is False


def test_http_error_is_a_failed_response_not_an_exception(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SENTINEL)
    _http(monkeypatch, _FakeResponse(status=429, text="rate limited"))
    r = pv.OpenAIAdapter().complete("p")
    assert r.ok is False and "429" in r.error and r.provider == "openai"


# ------------------------------------------------------------------ fallback
def test_complete_uses_the_chosen_provider_when_it_works(monkeypatch):
    fakes = _registry(monkeypatch, gemini=FakeAdapter("gemini"),
                      openai=FakeAdapter("openai"))
    r = pv.complete("p", provider="openai")
    assert r.ok and r.provider == "openai"
    assert fakes["gemini"].calls == []


def test_complete_falls_back_to_the_next_credentialled_provider(monkeypatch):
    attempts = []
    fakes = _registry(
        monkeypatch,
        gemini=FakeAdapter("gemini", ok=False, error="quota", attempts=attempts),
        openai=FakeAdapter("openai", creds=False, attempts=attempts),   # no key: skipped
        anthropic=FakeAdapter("anthropic", text="ANSWER", attempts=attempts),
        ollama=FakeAdapter("ollama", attempts=attempts),
    )
    r = pv.complete("p")                        # default provider = gemini
    assert r.ok and r.provider == "anthropic" and r.text == "ANSWER"
    assert attempts == ["gemini", "anthropic"]  # deterministic, skips keyless
    assert fakes["openai"].calls == []          # never called without credentials
    assert fakes["ollama"].calls == []          # stops at the first success


def test_fallback_false_tries_only_the_chosen_provider(monkeypatch):
    fakes = _registry(monkeypatch,
                      gemini=FakeAdapter("gemini", ok=False, error="quota"),
                      anthropic=FakeAdapter("anthropic"))
    r = pv.complete("p", fallback=False)
    assert r.ok is False and r.provider == "gemini" and "quota" in r.error
    assert fakes["anthropic"].calls == []


def test_fallback_order_is_deterministic_and_logged(monkeypatch, caplog):
    attempts = []
    _registry(monkeypatch,
              gemini=FakeAdapter("gemini", ok=False, error="dead", attempts=attempts),
              openai=FakeAdapter("openai", ok=False, error="dead", attempts=attempts),
              anthropic=FakeAdapter("anthropic", attempts=attempts))
    with caplog.at_level(logging.WARNING, logger="technofunda.llm.providers"):
        first = pv.complete("p", provider="openai")
    order_1 = list(attempts)
    attempts.clear()
    second = pv.complete("p", provider="openai")

    assert first.provider == second.provider == "anthropic"
    assert order_1 == attempts == ["openai", "gemini", "anthropic"]  # chosen first
    assert "answered by anthropic" in caplog.text


def test_all_providers_failing_returns_one_failed_response(monkeypatch):
    _registry(monkeypatch,
              gemini=FakeAdapter("gemini", ok=False, error="quota"),
              openai=FakeAdapter("openai", ok=False, error="401"))
    r = pv.complete("p")
    assert r.ok is False and r.provider == "gemini" and r.text == ""
    assert "quota" in r.error and "401" in r.error


def test_a_raising_adapter_cannot_stop_the_chain(monkeypatch):
    class Exploding(FakeAdapter):
        def complete(self, prompt, **kw):
            raise ValueError("boom")

    _registry(monkeypatch, gemini=Exploding("gemini"), openai=FakeAdapter("openai"))
    r = pv.complete("p")
    assert r.ok and r.provider == "openai"


def test_complete_forwards_its_kwargs_to_the_adapter(monkeypatch):
    fakes = _registry(monkeypatch, gemini=FakeAdapter("gemini"))
    pv.complete("prompt-text", json_mode=False, temperature=0.7, max_tokens=128)
    assert fakes["gemini"].calls == [{"prompt": "prompt-text", "json_mode": False,
                                      "temperature": 0.7, "max_tokens": 128}]


def test_complete_rejects_an_unknown_provider(monkeypatch):
    _registry(monkeypatch, gemini=FakeAdapter("gemini"))
    with pytest.raises(base.LLMError):
        pv.complete("p", provider="nope")


def test_available_lists_only_credentialled_providers(monkeypatch):
    _registry(monkeypatch, gemini=FakeAdapter("gemini", creds=False),
              openai=FakeAdapter("openai"), ollama=FakeAdapter("ollama", creds=False))
    assert pv.available() == ["openai"]


# ------------------------------------------------------------- key never leaks
def test_no_adapter_returns_or_logs_its_key(monkeypatch, caplog):
    """A provider that echoes the key back must not put it in an artefact/log."""
    monkeypatch.setenv("OPENAI_API_KEY", SENTINEL)
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_SENTINEL)
    # the API rejects the key and quotes it back in the body — the classic leak
    _http(monkeypatch, _FakeResponse(status=401,
                                     text=f'{{"error": "invalid key {SENTINEL}"}}'))
    monkeypatch.setattr(ia, "_gemini", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError(f"401 API key not valid: {GEMINI_SENTINEL}")))

    with caplog.at_level(logging.DEBUG):
        openai_r = pv.OpenAIAdapter().complete("p")
        gemini_r = pv.GeminiAdapter().complete("p")
        chain_r = pv.complete("p", provider="openai", fallback=False)

    for r in (openai_r, gemini_r, chain_r):
        assert r.ok is False
        blob = str(r.as_dict())
        assert SENTINEL not in blob and GEMINI_SENTINEL not in blob
        assert "***" in r.error
    assert SENTINEL not in caplog.text and GEMINI_SENTINEL not in caplog.text
    assert "invalid key" in openai_r.error       # the diagnosis still survives


def test_adapter_repr_never_shows_the_key():
    ad = pv.OpenAIAdapter(api_key=SENTINEL, model="gpt-4o")
    assert SENTINEL not in repr(ad)
    assert "gpt-4o" in repr(ad) and "openai" in repr(ad)


def test_no_credentials_message_never_contains_a_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    r = pv.DeepSeekAdapter().complete("p")       # a DIFFERENT provider's key is set
    assert SENTINEL not in r.error and "DEEPSEEK_API_KEY" in r.error


def test_redaction_scrubs_key_shapes_but_keeps_the_message():
    msg = pv._redact(f"HTTP 401 for {SENTINEL} and {GEMINI_SENTINEL}")
    assert SENTINEL not in msg and GEMINI_SENTINEL not in msg
    assert msg.startswith("HTTP 401 for ")
    assert pv._redact("HTTP 503 overloaded") == "HTTP 503 overloaded"
    assert pv._redact("value is 42", "42") == "value is 42"   # too short to be a key
