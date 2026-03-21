# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Internal LLM call utility — routes to Bifrost or Ollama based on INTERNAL_LLM_PROVIDER.

Used by pipeline operations that need lightweight LLM intelligence:
- Query decomposition
- Claim extraction
- Contextual chunk summaries
- AI categorization (smart tier)
- Memory conflict resolution

NOT used for user-facing chat (that goes through /chat/stream → OpenRouter).
NOT used for verification (that uses dedicated VERIFICATION_MODEL).
"""

from __future__ import annotations

import logging
import os

import config
from utils.bifrost import call_bifrost, extract_content
from utils.circuit_breaker import CircuitOpenError, get_breaker

logger = logging.getLogger("ai-companion.internal_llm")


async def call_internal_llm(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.1,
    max_tokens: int = 500,
    response_format: dict | None = None,
) -> str:
    """Route internal LLM call to configured provider.

    Returns the assistant message content as a string.
    Falls back to Bifrost if Ollama is unavailable.
    """
    provider = getattr(config, "INTERNAL_LLM_PROVIDER", "bifrost")

    if provider == "ollama":
        return await _call_ollama(
            messages, temperature=temperature, max_tokens=max_tokens,
        )
    else:
        return await _call_bifrost(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )


async def _call_ollama(
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int,
) -> str:
    """Call local Ollama instance for internal operations."""
    import httpx

    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    model = getattr(config, "INTERNAL_LLM_MODEL", "") or "phi3:mini"
    breaker = get_breaker("ollama")

    async def _do_call() -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("message", {}).get("content", "")

    try:
        return await breaker.call(_do_call)
    except (CircuitOpenError, httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning("Ollama call failed (%s), falling back to Bifrost", e)
        return await _call_bifrost(
            messages, temperature=temperature, max_tokens=max_tokens,
        )


async def _call_bifrost(
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int,
    response_format: dict | None = None,
) -> str:
    """Call Bifrost LLM gateway for internal operations."""
    model = getattr(config, "INTERNAL_LLM_MODEL", "") or None  # None = Bifrost default

    extra: dict = {}
    if response_format:
        extra["response_format"] = response_format

    data = await call_bifrost(
        messages=messages,
        breaker_name="bifrost-rerank",
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        extra_payload=extra if extra else None,
    )
    return extract_content(data)
