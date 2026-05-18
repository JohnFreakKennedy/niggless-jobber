"""Shared AI client wrapper with provider fallback.

Supported providers: openai, anthropic, gemini
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)

_VAULT_KEY_MAP = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

_DEFAULT_MODELS = {
    "openai": "gpt-4o",
    "anthropic": "claude-3-5-sonnet-20241022",
    "gemini": "gemini-2.0-flash",
}


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


class AIClient:
    def __init__(self, config: AIConfig):
        self.config = config

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
    ) -> str:
        provider = os.environ.get("NIGGLESS_AI_PROVIDER") or self.config.provider
        mt = max_tokens or self.config.max_tokens
        try:
            return await self._call_provider(provider, system_prompt, user_message, mt)
        except Exception as exc:
            log.warning("Primary provider %r failed (%s); trying fallback", provider, exc)
            await asyncio.sleep(10)
            fallback = self.config.fallback_provider
            fallback_model = self.config.fallback_model or _DEFAULT_MODELS.get(fallback, "")
            return await self._call_provider(
                fallback, system_prompt, user_message, mt,
                model_override=fallback_model,
            )

    async def _call_provider(
        self,
        provider: str,
        system_prompt: str,
        user_message: str,
        max_tokens: int,
        model_override: str | None = None,
    ) -> str:
        model = model_override or self.config.model or _DEFAULT_MODELS.get(provider, "")
        if provider == "openai":
            return await self._call_openai(system_prompt, user_message, max_tokens, model)
        if provider == "anthropic":
            return await self._call_anthropic(system_prompt, user_message, max_tokens, model)
        if provider == "gemini":
            return await self._call_gemini(system_prompt, user_message, max_tokens, model)
        raise ValueError(f"Unknown provider: {provider!r}. Supported: openai, anthropic, gemini")

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------
    async def _call_openai(
        self, system_prompt: str, user_message: str, max_tokens: int, model: str
    ) -> str:
        import openai
        client = openai.AsyncOpenAI(api_key=self._get_key("openai"))
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=max_tokens,
            temperature=self.config.temperature,
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
        self, system_prompt: str, user_message: str, max_tokens: int, model: str
    ) -> str:
        try:
            import google.generativeai as genai
        except ImportError:
            raise RuntimeError(
                "google-generativeai is not installed. "
                "Run: pip install google-generativeai"
            )
        genai.configure(api_key=self._get_key("gemini"))
        gemini_model = genai.GenerativeModel(
            model_name=model,
            system_instruction=system_prompt,
            generation_config=genai.GenerationConfig(
                max_output_tokens=max_tokens,
                temperature=self.config.temperature,
            ),
        )
        response = await gemini_model.generate_content_async(user_message)
        return response.text or ""
