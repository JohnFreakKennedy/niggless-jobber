"""Tests for cv_editor.client — Ollama provider, json_mode, env overrides, fallback.

No real network: Ollama calls go through ``httpx.MockTransport`` injected via
``AIClient._transport``; OpenAI is replaced by a fake ``openai.AsyncOpenAI``.
"""
from __future__ import annotations

import json
import sys
import types

import httpx
import pytest

from cv_editor.client import AIClient, AIConfig, _DEFAULT_MODELS


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("NIGGLESS_AI_PROVIDER", "NIGGLESS_AI_MODEL", "OLLAMA_HOST"):
        monkeypatch.delenv(var, raising=False)


def _ollama_cfg(**kw) -> AIConfig:
    base = dict(provider="ollama", model="llama3.1:8b", fallback_provider="none", fallback_delay=0)
    base.update(kw)
    return AIConfig(**base)


class _Recorder:
    """MockTransport handler that records requests and replies with a fixed response."""

    def __init__(self, status: int = 200, body: dict | None = None, exc: Exception | None = None):
        self.status = status
        self.body = body if body is not None else {"message": {"role": "assistant", "content": "hello"}}
        self.exc = exc
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.exc is not None:
            raise self.exc
        return httpx.Response(self.status, json=self.body)

    @property
    def payload(self) -> dict:
        return json.loads(self.requests[-1].content)


def _client(cfg: AIConfig, handler: _Recorder) -> AIClient:
    c = AIClient(cfg)
    c._transport = httpx.MockTransport(handler)
    return c


# ----------------------------------------------------------------------
# Ollama request shape
# ----------------------------------------------------------------------
async def test_ollama_returns_message_content():
    rec = _Recorder(body={"message": {"content": "tailored cv"}})
    out = await _client(_ollama_cfg(), rec).complete("sys", "user")
    assert out == "tailored cv"


async def test_ollama_payload_shape():
    rec = _Recorder()
    cfg = _ollama_cfg(temperature=0.2, ollama_num_ctx=4096)
    await _client(cfg, rec).complete("SYS", "USER", max_tokens=777)
    p = rec.payload
    assert p["model"] == "llama3.1:8b"
    assert p["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
    ]
    assert p["stream"] is False
    assert p["options"] == {"temperature": 0.2, "num_predict": 777, "num_ctx": 4096}
    assert "format" not in p


async def test_ollama_default_max_tokens_and_num_ctx():
    rec = _Recorder()
    await _client(_ollama_cfg(max_tokens=1234), rec).complete("s", "u")
    assert rec.payload["options"]["num_predict"] == 1234
    assert rec.payload["options"]["num_ctx"] == 8192


async def test_ollama_json_mode_sets_format():
    rec = _Recorder(body={"message": {"content": '{"ok": true}'}})
    out = await _client(_ollama_cfg(), rec).complete("s", "u", json_mode=True)
    assert rec.payload["format"] == "json"
    assert json.loads(out) == {"ok": True}


async def test_ollama_sends_no_auth_header():
    rec = _Recorder()
    await _client(_ollama_cfg(), rec).complete("s", "u")
    assert "authorization" not in {k.lower() for k in rec.requests[-1].headers}


async def test_ollama_missing_message_returns_empty_string():
    rec = _Recorder(body={"done": True})
    assert await _client(_ollama_cfg(), rec).complete("s", "u") == ""


# ----------------------------------------------------------------------
# Base URL / OLLAMA_HOST
# ----------------------------------------------------------------------
async def test_ollama_default_base_url():
    rec = _Recorder()
    await _client(_ollama_cfg(), rec).complete("s", "u")
    assert str(rec.requests[-1].url) == "http://localhost:11434/api/chat"


async def test_ollama_config_base_url_trailing_slash():
    rec = _Recorder()
    await _client(_ollama_cfg(ollama_base_url="http://gpu-box:11434/"), rec).complete("s", "u")
    assert str(rec.requests[-1].url) == "http://gpu-box:11434/api/chat"


async def test_ollama_config_base_url_without_scheme():
    rec = _Recorder()
    await _client(_ollama_cfg(ollama_base_url="gpu-box:9999"), rec).complete("s", "u")
    assert str(rec.requests[-1].url) == "http://gpu-box:9999/api/chat"


@pytest.mark.parametrize(
    "host, expected",
    [
        ("127.0.0.1:11500", "http://127.0.0.1:11500/api/chat"),
        ("http://10.0.0.5:11434", "http://10.0.0.5:11434/api/chat"),
        ("https://ollama.example.com", "https://ollama.example.com/api/chat"),
    ],
)
async def test_ollama_host_env_overrides_config(monkeypatch, host, expected):
    monkeypatch.setenv("OLLAMA_HOST", host)
    rec = _Recorder()
    await _client(_ollama_cfg(ollama_base_url="http://ignored:1"), rec).complete("s", "u")
    assert str(rec.requests[-1].url) == expected


# ----------------------------------------------------------------------
# Ollama errors
# ----------------------------------------------------------------------
async def test_ollama_connection_error_message():
    rec = _Recorder(exc=httpx.ConnectError("Connection refused"))
    with pytest.raises(RuntimeError, match="ollama serve") as ei:
        await _client(_ollama_cfg(), rec).complete("s", "u")
    assert "http://localhost:11434" in str(ei.value)


async def test_ollama_404_suggests_pull():
    rec = _Recorder(status=404, body={"error": "model 'qwen:7b' not found"})
    with pytest.raises(RuntimeError, match=r"ollama pull qwen:7b"):
        await _client(_ollama_cfg(model="qwen:7b"), rec).complete("s", "u")


async def test_ollama_500_reports_status():
    rec = _Recorder(status=500, body={"error": "out of memory"})
    with pytest.raises(RuntimeError, match="HTTP 500.*out of memory"):
        await _client(_ollama_cfg(), rec).complete("s", "u")


async def test_ollama_timeout_message():
    rec = _Recorder(exc=httpx.ReadTimeout("slow"))
    with pytest.raises(RuntimeError, match="timed out after 5"):
        await _client(_ollama_cfg(request_timeout=5), rec).complete("s", "u")


async def test_unknown_provider_lists_ollama():
    c = AIClient(AIConfig(provider="bogus", fallback_provider="none"))
    with pytest.raises(ValueError, match="Supported: .*ollama"):
        await c.complete("s", "u")


# ----------------------------------------------------------------------
# Env provider / model overrides
# ----------------------------------------------------------------------
async def test_env_provider_differs_uses_provider_default_model(monkeypatch):
    monkeypatch.setenv("NIGGLESS_AI_PROVIDER", "ollama")
    rec = _Recorder()
    cfg = AIConfig(provider="openai", model="gpt-4o", fallback_provider="none")
    await _client(cfg, rec).complete("s", "u")
    assert rec.payload["model"] == _DEFAULT_MODELS["ollama"]


async def test_env_model_wins(monkeypatch):
    monkeypatch.setenv("NIGGLESS_AI_PROVIDER", "ollama")
    monkeypatch.setenv("NIGGLESS_AI_MODEL", "gemma3:latest")
    rec = _Recorder()
    cfg = AIConfig(provider="openai", model="gpt-4o", fallback_provider="none")
    await _client(cfg, rec).complete("s", "u")
    assert rec.payload["model"] == "gemma3:latest"


async def test_env_provider_same_as_config_keeps_config_model(monkeypatch):
    monkeypatch.setenv("NIGGLESS_AI_PROVIDER", "ollama")
    rec = _Recorder()
    await _client(_ollama_cfg(model="mistral:7b"), rec).complete("s", "u")
    assert rec.payload["model"] == "mistral:7b"


async def test_env_model_alone_overrides_model(monkeypatch):
    monkeypatch.setenv("NIGGLESS_AI_MODEL", "phi3:mini")
    rec = _Recorder()
    await _client(_ollama_cfg(), rec).complete("s", "u")
    assert rec.payload["model"] == "phi3:mini"


# ----------------------------------------------------------------------
# Fallback
# ----------------------------------------------------------------------
@pytest.mark.parametrize("fb", ["none", "None", "", None])
async def test_fallback_none_reraises_original(fb):
    rec = _Recorder(status=404)
    c = _client(_ollama_cfg(fallback_provider=fb), rec)
    with pytest.raises(RuntimeError, match="ollama pull"):
        await c.complete("s", "u")
    assert len(rec.requests) == 1


async def test_fallback_same_provider_same_model_reraises():
    rec = _Recorder(status=404)
    cfg = _ollama_cfg(fallback_provider="ollama", fallback_model="llama3.1:8b")
    with pytest.raises(RuntimeError, match="ollama pull"):
        await _client(cfg, rec).complete("s", "u")
    assert len(rec.requests) == 1


async def test_fallback_same_provider_other_model_is_tried():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == "big:70b":
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json={"message": {"content": f"from {model}"}})

    cfg = _ollama_cfg(model="big:70b", fallback_provider="ollama", fallback_model="small:3b")
    c = AIClient(cfg)
    c._transport = httpx.MockTransport(handler)
    assert await c.complete("s", "u", json_mode=True) == "from small:3b"
    assert calls == ["big:70b", "small:3b"]


async def test_fallback_used_on_primary_failure(monkeypatch):
    seen: dict = {}

    async def fake_ollama(self, system_prompt, user_message, max_tokens, model, json_mode=False):
        seen.update(model=model, json_mode=json_mode, max_tokens=max_tokens)
        return "local answer"

    async def failing_openai(self, *a, **kw):
        raise RuntimeError("openai down")

    monkeypatch.setattr(AIClient, "_call_ollama", fake_ollama)
    monkeypatch.setattr(AIClient, "_call_openai", failing_openai)
    cfg = AIConfig(provider="openai", model="gpt-4o", fallback_provider="ollama",
                   fallback_model="", fallback_delay=0)
    out = await AIClient(cfg).complete("s", "u", max_tokens=50, json_mode=True)
    assert out == "local answer"
    assert seen == {"model": _DEFAULT_MODELS["ollama"], "json_mode": True, "max_tokens": 50}


async def test_fallback_delay_is_used(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(sec):
        slept.append(sec)

    monkeypatch.setattr("cv_editor.client.asyncio.sleep", fake_sleep)

    async def fail(self, *a, **kw):
        raise RuntimeError("down")

    async def ok(self, *a, **kw):
        return "ok"

    monkeypatch.setattr(AIClient, "_call_openai", fail)
    monkeypatch.setattr(AIClient, "_call_anthropic", ok)
    assert await AIClient(AIConfig(fallback_delay=2.5)).complete("s", "u") == "ok"
    assert slept == [2.5]

    slept.clear()
    assert await AIClient(AIConfig(fallback_delay=0)).complete("s", "u") == "ok"
    assert slept == []


# ----------------------------------------------------------------------
# AIConfig.from_dict
# ----------------------------------------------------------------------
def test_from_dict_empty_gives_defaults():
    assert AIConfig.from_dict({}) == AIConfig()
    assert AIConfig.from_dict(None) == AIConfig()


def test_from_dict_reads_all_keys():
    raw = {
        "provider": "ollama", "model": "gemma3:latest",
        "fallback_provider": "none", "fallback_model": "x",
        "temperature": 0.1, "max_tokens": 2000, "cv_max_tokens": 3000,
        "cover_letter_max_tokens": 900, "ollama_base_url": "http://box:1",
        "ollama_num_ctx": 16384, "request_timeout": 600,
        "concurrency": 3,  # unrelated key, ignored
    }
    cfg = AIConfig.from_dict(raw)
    assert (cfg.provider, cfg.model) == ("ollama", "gemma3:latest")
    assert (cfg.fallback_provider, cfg.fallback_model) == ("none", "x")
    assert cfg.temperature == 0.1
    assert (cfg.max_tokens, cfg.cv_max_tokens, cfg.cover_letter_max_tokens) == (2000, 3000, 900)
    assert cfg.ollama_base_url == "http://box:1"
    assert cfg.ollama_num_ctx == 16384
    assert cfg.request_timeout == 600


def test_from_dict_yaml_null_fallback_means_none():
    assert AIConfig.from_dict({"fallback_provider": None}).fallback_provider == "none"


def test_from_dict_provider_without_model_uses_provider_default():
    assert AIConfig.from_dict({"provider": "ollama"}).model == "llama3.1:8b"


def test_from_dict_env_provider_override(monkeypatch):
    monkeypatch.setenv("NIGGLESS_AI_PROVIDER", "gemini")
    cfg = AIConfig.from_dict({"provider": "openai", "model": "gpt-4o"})
    assert (cfg.provider, cfg.model) == ("gemini", "gemini-3.8-flash")


def test_from_dict_env_provider_and_model(monkeypatch):
    monkeypatch.setenv("NIGGLESS_AI_PROVIDER", "ollama")
    monkeypatch.setenv("NIGGLESS_AI_MODEL", "gemma3:latest")
    cfg = AIConfig.from_dict({"provider": "openai", "model": "gpt-4o"})
    assert (cfg.provider, cfg.model) == ("ollama", "gemma3:latest")


# ----------------------------------------------------------------------
# OpenAI json_mode
# ----------------------------------------------------------------------
class _FakeOpenAI:
    last_kwargs: dict = {}

    def __init__(self, api_key=None, **kw):
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        _FakeOpenAI.last_kwargs = kwargs
        msg = types.SimpleNamespace(content='{"a": 1}')
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


@pytest.fixture()
def fake_openai(monkeypatch):
    import openai
    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    monkeypatch.setattr(AIClient, "_get_key", lambda self, provider: "sk-test")
    _FakeOpenAI.last_kwargs = {}
    return _FakeOpenAI


async def test_openai_json_mode_passes_response_format(fake_openai):
    cfg = AIConfig(fallback_provider="none")
    out = await AIClient(cfg).complete("s", "u", json_mode=True)
    assert out == '{"a": 1}'
    assert fake_openai.last_kwargs["response_format"] == {"type": "json_object"}
    assert fake_openai.last_kwargs["model"] == "gpt-4o"


async def test_openai_without_json_mode_has_no_response_format(fake_openai):
    await AIClient(AIConfig(fallback_provider="none")).complete("s", "u")
    assert "response_format" not in fake_openai.last_kwargs


# ----------------------------------------------------------------------
# Gemini (fake google.genai client)
# ----------------------------------------------------------------------
def _fake_genai(monkeypatch, captured: dict, text: str = "{}"):
    from google.genai import types as genai_types

    class _Models:
        async def generate_content(self, **kw):
            captured["call"] = kw
            return types.SimpleNamespace(text=text)

    class _Client:
        def __init__(self, **kw):
            captured["client"] = kw
            self.aio = types.SimpleNamespace(models=_Models())

    import google.genai
    monkeypatch.setattr(google.genai, "Client", _Client)
    monkeypatch.setattr(AIClient, "_get_key", lambda self, provider: "k")
    return genai_types


async def test_gemini_json_mode_sets_mime_type(monkeypatch):
    captured: dict = {}
    _fake_genai(monkeypatch, captured)
    cfg = AIConfig(provider="gemini", model="gemini-3.8-flash", fallback_provider="none")
    assert await AIClient(cfg).complete("s", "u", json_mode=True) == "{}"
    assert captured["call"]["config"].response_mime_type == "application/json"


async def test_gemini_request_shape(monkeypatch):
    captured: dict = {}
    _fake_genai(monkeypatch, captured, text="hello")
    cfg = AIConfig(provider="gemini", model="gemini-3.8-flash", fallback_provider="none",
                   temperature=0.2)
    assert await AIClient(cfg).complete("system text", "user text", max_tokens=123) == "hello"
    call = captured["call"]
    assert call["model"] == "gemini-3.8-flash"
    assert call["contents"] == "user text"
    assert call["config"].system_instruction == "system text"
    assert call["config"].max_output_tokens == 123
    assert call["config"].temperature == 0.2
    assert call["config"].response_mime_type is None
    assert captured["client"]["api_key"] == "k"
    assert captured["client"]["http_options"].retry_options.attempts == 1


async def test_hosted_call_that_hangs_times_out(monkeypatch):
    import asyncio
    from cv_editor.client import AIClient, AIConfig

    client = AIClient(AIConfig(provider="openai", model="gpt-4o", fallback_provider="none",
                               request_timeout=0.05))

    async def hang(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(client, "_call_openai", hang)
    with pytest.raises(RuntimeError, match="did not answer within"):
        await client.complete("sys", "user")


async def test_gemini_thinking_budget_sent(monkeypatch):
    captured: dict = {}
    _fake_genai(monkeypatch, captured)
    cfg = AIConfig(provider="gemini", model="gemini-3.8-flash", fallback_provider="none")
    await AIClient(cfg).complete("s", "u")
    assert captured["call"]["config"].thinking_config.thinking_budget == 0
    cfg = AIConfig.from_dict({"provider": "gemini", "thinking_budget": -1, "fallback_provider": None})
    await AIClient(cfg).complete("s", "u")
    assert captured["call"]["config"].thinking_config.thinking_budget == -1


async def test_gemini_empty_reply_raises(monkeypatch):
    captured: dict = {}
    _fake_genai(monkeypatch, captured, text="")
    cfg = AIConfig(provider="gemini", model="gemini-3.8-flash", fallback_provider="none")
    with pytest.raises(RuntimeError, match="returned no text"):
        await AIClient(cfg).complete("s", "u")


async def test_gemini_retries_without_thinking_on_400(monkeypatch):
    calls: list = []

    class _Err(Exception):
        code = 400

    class _Models:
        async def generate_content(self, **kw):
            calls.append(kw["config"].thinking_config)
            if kw["config"].thinking_config is not None:
                raise _Err("400 INVALID_ARGUMENT")
            return types.SimpleNamespace(text="ok")

    class _Client:
        def __init__(self, **kw):
            self.aio = types.SimpleNamespace(models=_Models())

    import google.genai
    monkeypatch.setattr(google.genai, "Client", _Client)
    monkeypatch.setattr(AIClient, "_get_key", lambda self, provider: "k")
    cfg = AIConfig(provider="gemini", model="gemini-flash-lite-latest", fallback_provider="none")
    client = AIClient(cfg)
    assert await client.complete("s", "u") == "ok"
    assert calls[0] is not None and calls[1] is None
    # Remembered: the second request goes out without the thinking setting.
    assert await client.complete("s", "u") == "ok"
    assert len(calls) == 3 and calls[2] is None


async def test_gemini_other_errors_not_retried(monkeypatch):
    calls: list = []

    class _Err(Exception):
        code = 429

    class _Models:
        async def generate_content(self, **kw):
            calls.append(1)
            raise _Err("429 RESOURCE_EXHAUSTED")

    class _Client:
        def __init__(self, **kw):
            self.aio = types.SimpleNamespace(models=_Models())

    import google.genai
    monkeypatch.setattr(google.genai, "Client", _Client)
    monkeypatch.setattr(AIClient, "_get_key", lambda self, provider: "k")
    cfg = AIConfig(provider="gemini", model="gemini-3.8-flash", fallback_provider="none")
    with pytest.raises(_Err):
        await AIClient(cfg).complete("s", "u")
    assert calls == [1]


def test_gemini_empty_reason_messages():
    from cv_editor.client import _gemini_empty_reason
    ns = types.SimpleNamespace
    blocked = ns(prompt_feedback=ns(block_reason="SAFETY"), candidates=[], usage_metadata=None)
    assert "blocked (SAFETY)" in _gemini_empty_reason(blocked)
    capped = ns(prompt_feedback=None, candidates=[ns(finish_reason="FinishReason.MAX_TOKENS")],
                usage_metadata=ns(thoughts_token_count=46))
    assert "46 thinking tokens" in _gemini_empty_reason(capped)
    assert _gemini_empty_reason(ns(prompt_feedback=None, candidates=[], usage_metadata=None)) == \
        "no candidates returned"


async def test_gemini_client_reused(monkeypatch):
    captured: dict = {}
    made: list = []
    _fake_genai(monkeypatch, captured, text="ok")
    import google.genai
    real = google.genai.Client
    monkeypatch.setattr(google.genai, "Client", lambda **kw: made.append(1) or real(**kw))
    client = AIClient(AIConfig(provider="gemini", model="gemini-3.8-flash", fallback_provider="none"))
    await client.complete("s", "u")
    await client.complete("s", "u")
    assert made == [1]
