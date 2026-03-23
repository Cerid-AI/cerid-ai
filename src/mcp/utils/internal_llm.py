# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Internal LLM call utility — routes to direct OpenRouter or Ollama based on INTERNAL_LLM_PROVIDER.

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
    Falls back through: Ollama → direct OpenRouter → Bifrost.
    """
    provider = getattr(config, "INTERNAL_LLM_PROVIDER", "bifrost")

    if provider == "ollama":
        return await _call_ollama(
            messages, temperature=temperature, max_tokens=max_tokens,
        )
    else:
        # Default: direct OpenRouter via unified client
        from utils.llm_client import call_llm
        return await call_llm(
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
    model = getattr(config, "INTERNAL_LLM_MODEL", "") or config.OLLAMA_DEFAULT_MODEL
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
        logger.warning("Ollama call failed (%s), falling back to direct OpenRouter", e)
        from utils.llm_client import call_llm
        return await call_llm(
            messages, temperature=temperature, max_tokens=max_tokens,
        )
