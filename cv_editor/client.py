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
    # HTTP timeout in seconds for local (Ollama) requests; local models are slow.
    request_timeout: float = 300.0

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


class AIClient:
    def __init__(self, config: AIConfig):
        self.config = config
        # Tests inject an ``httpx.MockTransport`` here; None = real network.
        self._transport: httpx.AsyncBaseTransport | None = None

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
            import google.generativeai as genai
        except ImportError:
            raise RuntimeError(
                "google-generativeai is not installed. "
                "Run: pip install google-generativeai"
            )
        genai.configure(api_key=self._get_key("gemini"))
        gen_kwargs: dict[str, Any] = {
            "max_output_tokens": max_tokens,
            "temperature": self.config.temperature,
        }
        if json_mode:
            gen_kwargs["response_mime_type"] = "application/json"
        gemini_model = genai.GenerativeModel(
            model_name=model,
            system_instruction=system_prompt,
            generation_config=genai.GenerationConfig(**gen_kwargs),
        )
        response = await gemini_model.generate_content_async(user_message)
        return response.text or ""

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
