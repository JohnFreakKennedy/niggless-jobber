"""Shared AI client wrapper with provider fallback.

Supported providers: openai, anthropic, gemini, ollama (local, no API key)
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, fields
from typing import Any

import httpx

log = logging.getLogger(__name__)

_VAULT_KEY_MAP = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

_DEFAULT_MODELS = {
    "openai": "gpt-4o",
    "anthropic": "claude-3-5-sonnet-20241022",
    "gemini": "gemini-3.8-flash",
    "ollama": "llama3.1:8b",
}

SUPPORTED_PROVIDERS = ("openai", "anthropic", "gemini", "ollama")

_DEFAULT_OLLAMA_URL = "http://localhost:11434"

# Values of fallback_provider that mean "no fallback".
_NO_FALLBACK = {"", "none", "null", "off", "false"}


def _normalize_base_url(url: str) -> str:
    """Accept ``host:port``, ``http://host:port`` or ``https://...``; return a URL with scheme."""
    url = (url or "").strip()
    if not url:
        return _DEFAULT_OLLAMA_URL
    if "://" not in url:
        url = "http://" + url
    return url.rstrip("/")


@dataclass
class AIConfig:
    provider: str = "openai"
    model: str = "gpt-4o"
    fallback_provider: str = "anthropic"
    fallback_model: str = "claude-3-5-sonnet-20241022"
    temperature: float = 0.3
    max_tokens: int = 4096
    cv_max_tokens: int = 4096
    cover_letter_max_tokens: int = 1024
    # Seconds to wait before trying the fallback provider (tests set 0).
    fallback_delay: float = 10.0
    # Ollama (local models)
    ollama_base_url: str = _DEFAULT_OLLAMA_URL
    ollama_num_ctx: int = 8192
    # Timeout in seconds for one request to any provider; local models are slow.
    request_timeout: float = 300.0
    # Gemini "thinking" tokens count against max_tokens and can use all of it,
    # leaving an empty reply. 0 turns thinking off (Flash models); -1 lets the
    # model decide (needed for Pro models, which cannot turn it off).
    thinking_budget: int = 0

    @classmethod
    def from_dict(cls, raw: dict | None) -> "AIConfig":
        """Build an AIConfig from the ``ai:`` section of config.yaml.

        Missing keys keep the dataclass defaults.  Env overrides:
        ``NIGGLESS_AI_PROVIDER`` replaces the provider; ``NIGGLESS_AI_MODEL``
        replaces the model.  If the provider is overridden to a different
        one and no model override is given, the provider's default model is
        used (so e.g. gpt-4o is never sent to gemini).
        """
        raw = raw or {}
        known = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {k: v for k, v in raw.items() if k in known and v is not None}
        # YAML ``fallback_provider: none`` / ``null`` both mean "no fallback".
        if "fallback_provider" in raw and raw["fallback_provider"] is None:
            kwargs["fallback_provider"] = "none"
        cfg = cls(**kwargs)
        # When the config names a provider but no model, use that provider's default.
        if "provider" in raw and not raw.get("model"):
            cfg.model = _DEFAULT_MODELS.get(cfg.provider, cfg.model)
        cfg.provider, cfg.model = _resolve_provider_model(cfg.provider, cfg.model)
        return cfg


def _resolve_provider_model(cfg_provider: str, cfg_model: str) -> tuple[str, str]:
    """Apply NIGGLESS_AI_PROVIDER / NIGGLESS_AI_MODEL env overrides."""
    env_provider = (os.environ.get("NIGGLESS_AI_PROVIDER") or "").strip()
    env_model = (os.environ.get("NIGGLESS_AI_MODEL") or "").strip()
    provider = env_provider or cfg_provider
    if env_model:
        model = env_model
    elif env_provider and env_provider != cfg_provider:
        model = _DEFAULT_MODELS.get(provider, "")
    else:
        model = cfg_model or _DEFAULT_MODELS.get(provider, "")
    return provider, model


def _gemini_empty_reason(response) -> str:
    """Why a Gemini reply has no text, in words a user can act on."""
    feedback = getattr(response, "prompt_feedback", None)
    block = getattr(feedback, "block_reason", None)
    if block:
        return f"the prompt was blocked ({block})"
    candidates = getattr(response, "candidates", None) or []
    finish = getattr(candidates[0], "finish_reason", None) if candidates else None
    thoughts = getattr(getattr(response, "usage_metadata", None), "thoughts_token_count", None)
    if finish is not None and "MAX_TOKENS" in str(finish):
        if thoughts:
            return (f"the output limit was reached after {thoughts} thinking tokens; "
                    "raise ai.cv_max_tokens / cover_letter_max_tokens or lower ai.thinking_budget")
        return "the output limit was reached; raise ai.cv_max_tokens / cover_letter_max_tokens"
    return f"finish reason {finish}" if finish is not None else "no candidates returned"


class AIClient:
    def __init__(self, config: AIConfig):
        self.config = config
        # Tests inject an ``httpx.MockTransport`` here; None = real network.
        self._transport: httpx.AsyncBaseTransport | None = None
        # Gemini models that rejected a thinking setting; it is not sent again.
        self._gemini_no_thinking: set[str] = set()
        self._gemini_client = None

    def _get_key(self, provider: str) -> str:
        from storage.vault import get_secret
        vault_key = _VAULT_KEY_MAP.get(provider)
        if not vault_key:
            raise ValueError(f"Unknown provider: {provider!r}")
        key = get_secret(vault_key) or os.environ.get(vault_key, "")
        if not key:
            raise RuntimeError(
                f"{vault_key} not found in vault. "
                f"Run: python run.py --vault-set {vault_key} <key>"
            )
        return key

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        """Return the model's text reply.

        ``json_mode=True`` asks the provider for a JSON object where the API
        supports it (openai, gemini, ollama); anthropic has no such flag.
        """
        provider, model = _resolve_provider_model(self.config.provider, self.config.model)
        mt = max_tokens or self.config.max_tokens
        try:
            return await self._call_provider(
                provider, system_prompt, user_message, mt,
                model_override=model, json_mode=json_mode,
            )
        except Exception as exc:
            fallback = (self.config.fallback_provider or "").strip()
            if fallback.lower() in _NO_FALLBACK:
                raise
            fallback_model = self.config.fallback_model or _DEFAULT_MODELS.get(fallback, "")
            if fallback == provider and fallback_model == model:
                raise
            log.warning("Primary provider %r failed (%s); trying fallback %r", provider, exc, fallback)
            if self.config.fallback_delay > 0:
                await asyncio.sleep(self.config.fallback_delay)
            return await self._call_provider(
                fallback, system_prompt, user_message, mt,
                model_override=fallback_model, json_mode=json_mode,
            )

    async def _call_provider(
        self,
        provider: str,
        system_prompt: str,
        user_message: str,
        max_tokens: int,
        model_override: str | None = None,
        json_mode: bool = False,
    ) -> str:
        model = model_override or self.config.model or _DEFAULT_MODELS.get(provider, "")
        hosted = {
            "openai": lambda: self._call_openai(system_prompt, user_message, max_tokens, model, json_mode),
            "anthropic": lambda: self._call_anthropic(system_prompt, user_message, max_tokens, model),
            "gemini": lambda: self._call_gemini(system_prompt, user_message, max_tokens, model, json_mode),
        }
        if provider in hosted:
            # SDK defaults can wait for many minutes on a stuck connection; a
            # hung call would stall the whole run, so bound it.
            try:
                return await asyncio.wait_for(hosted[provider](), timeout=self.config.request_timeout)
            except asyncio.TimeoutError:
                raise RuntimeError(
                    f"{provider} ({model!r}) did not answer within {self.config.request_timeout}s; "
                    "raise ai.request_timeout if this model is slow"
                ) from None
        if provider == "ollama":
            return await self._call_ollama(system_prompt, user_message, max_tokens, model, json_mode)
        raise ValueError(
            f"Unknown provider: {provider!r}. Supported: {', '.join(SUPPORTED_PROVIDERS)}"
        )

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------
    async def _call_openai(
        self, system_prompt: str, user_message: str, max_tokens: int, model: str,
        json_mode: bool = False,
    ) -> str:
        import openai
        client = openai.AsyncOpenAI(api_key=self._get_key("openai"))
        extra: dict[str, Any] = {}
        if json_mode:
            extra["response_format"] = {"type": "json_object"}
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=max_tokens,
            temperature=self.config.temperature,
            **extra,
        )
        return resp.choices[0].message.content or ""

    # ------------------------------------------------------------------
    # Anthropic
    # ------------------------------------------------------------------
    async def _call_anthropic(
        self, system_prompt: str, user_message: str, max_tokens: int, model: str
    ) -> str:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=self._get_key("anthropic"))
        resp = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return resp.content[0].text if resp.content else ""

    # ------------------------------------------------------------------
    # Google Gemini
    # ------------------------------------------------------------------
    async def _call_gemini(
        self, system_prompt: str, user_message: str, max_tokens: int, model: str,
        json_mode: bool = False,
    ) -> str:
        try:
            from google import genai
            from google.genai import types as genai_types
        except ImportError:
            raise RuntimeError("google-genai is not installed. Run: pip install google-genai")
        if self._gemini_client is None:
            # One attempt: a 429 (quota used up) should fail fast and reach the
            # fallback provider instead of being retried quietly for minutes.
            self._gemini_client = genai.Client(
                api_key=self._get_key("gemini"),
                http_options=genai_types.HttpOptions(
                    retry_options=genai_types.HttpRetryOptions(attempts=1),
                ),
            )
        client = self._gemini_client
        gen_kwargs: dict[str, Any] = {
            "system_instruction": system_prompt,
            "max_output_tokens": max_tokens,
            "temperature": self.config.temperature,
        }
        if model not in self._gemini_no_thinking:
            gen_kwargs["thinking_config"] = genai_types.ThinkingConfig(
                thinking_budget=self.config.thinking_budget
            )
        if json_mode:
            gen_kwargs["response_mime_type"] = "application/json"

        async def generate():
            return await client.aio.models.generate_content(
                model=model,
                contents=user_message,
                config=genai_types.GenerateContentConfig(**gen_kwargs),
            )

        try:
            response = await generate()
        except Exception as exc:
            # Models without thinking (e.g. Flash-Lite) reject any thinking
            # setting with a bare 400 INVALID_ARGUMENT; retry without it.
            if getattr(exc, "code", None) != 400 or "thinking_config" not in gen_kwargs:
                raise
            log.info("gemini %r rejected the thinking setting; retrying without it", model)
            gen_kwargs.pop("thinking_config")
            response = await generate()
            self._gemini_no_thinking.add(model)
        if not response.text:
            raise RuntimeError(f"gemini ({model!r}) returned no text: {_gemini_empty_reason(response)}")
        return response.text

    # ------------------------------------------------------------------
    # Ollama (local models, no API key)
    # ------------------------------------------------------------------
    def _ollama_base_url(self) -> str:
        return _normalize_base_url(os.environ.get("OLLAMA_HOST") or self.config.ollama_base_url)

    def _ollama_payload(
        self, system_prompt: str, user_message: str, max_tokens: int, model: str,
        json_mode: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "options": {
                "temperature": self.config.temperature,
                "num_predict": max_tokens,
                "num_ctx": self.config.ollama_num_ctx,
            },
        }
        if json_mode:
            payload["format"] = "json"
        return payload

    async def _call_ollama(
        self, system_prompt: str, user_message: str, max_tokens: int, model: str,
        json_mode: bool = False,
    ) -> str:
        base_url = self._ollama_base_url()
        url = f"{base_url}/api/chat"
        payload = self._ollama_payload(system_prompt, user_message, max_tokens, model, json_mode)
        try:
            async with httpx.AsyncClient(
                timeout=self.config.request_timeout, transport=self._transport,
            ) as client:
                resp = await client.post(url, json=payload)
        except httpx.ConnectError as exc:
            raise RuntimeError(
                f"Cannot reach Ollama at {base_url} ({exc}). "
                "Is `ollama serve` running? Set ai.ollama_base_url or OLLAMA_HOST if it "
                "listens elsewhere."
            ) from exc
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f"Ollama at {base_url} timed out after {self.config.request_timeout}s "
                f"(model {model!r}). Raise ai.request_timeout or use a smaller model."
            ) from exc

        if resp.status_code == 404:
            raise RuntimeError(
                f"Ollama model {model!r} not found at {base_url}. "
                f"Run: ollama pull {model}"
            )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Ollama returned HTTP {resp.status_code}: {resp.text[:500]}"
            )
        data = resp.json()
        return (data.get("message") or {}).get("content") or ""
