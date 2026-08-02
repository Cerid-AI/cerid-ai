# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Concrete LLMClient implementations for the configured providers.

- :class:`OpenRouterLLMClient` — cloud aggregator path (the default).
- :class:`OllamaLLMClient` — local backend speaking the Ollama HTTP wire
  format. Used for both ``INTERNAL_LLM_PROVIDER=ollama`` and
  ``=quenchforge`` since they share the protocol; the constructor picks
  which URL to hit.
"""

from __future__ import annotations

from core.contracts.llm import LLMClient, LLMResponse


class OpenRouterLLMClient(LLMClient):
    """LLMClient that delegates to the existing core/utils/llm_client.call_llm()."""

    async def call(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2000,
        breaker_name: str = "default",
    ) -> LLMResponse:
        from core.utils.llm_client import call_llm
        result = await call_llm(
            messages=messages, model=model or "",
            temperature=temperature, max_tokens=max_tokens,
            breaker_name=breaker_name,
        )
        return LLMResponse(
            content=result,
            model=model or "unknown",
            usage=None,
        )


class OllamaLLMClient(LLMClient):
    """LLMClient that speaks the Ollama HTTP wire format.

    Backs both ``INTERNAL_LLM_PROVIDER=ollama`` and ``=quenchforge`` — the
    payload is identical; the constructor picks which URL is hit (``OLLAMA_URL``
    vs ``QUENCHFORGE_URL``). Falls back to OpenRouter on connect/timeout/HTTP
    errors via the same path used by :func:`call_internal_llm`.
    """

    def __init__(self, provider: str = "ollama") -> None:
        if provider not in ("ollama", "quenchforge"):
            raise ValueError(
                f"OllamaLLMClient supports 'ollama' or 'quenchforge', got {provider!r}"
            )
        self._provider = provider

    async def call(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2000,
        breaker_name: str = "default",  # noqa: ARG002 — circuit breaker is keyed by _call_ollama
    ) -> LLMResponse:
        from core.utils.internal_llm import _call_ollama
        content = await _call_ollama(
            messages,
            provider=self._provider,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=False,
        )
        return LLMResponse(
            content=content,
            model=model or f"{self._provider}-default",
            usage=None,
        )
