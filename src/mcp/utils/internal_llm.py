# Copyright (c) 2026 Cerid AI. All rights reserved.
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

import httpx

import config
from utils.circuit_breaker import CircuitOpenError, get_breaker

logger = logging.getLogger("ai-companion.internal_llm")

# Shared connection pool for Ollama calls (avoids per-request TCP handshake)
_ollama_client: httpx.AsyncClient | None = None


def _get_ollama_client() -> httpx.AsyncClient:
    global _ollama_client
    if _ollama_client is None or _ollama_client.is_closed:
        _ollama_client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _ollama_client


async def close_ollama_client() -> None:
    global _ollama_client
    if _ollama_client and not _ollama_client.is_closed:
        await _ollama_client.aclose()
        _ollama_client = None


async def call_internal_llm(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.1,
    max_tokens: int = 500,
    response_format: dict | None = None,
    stage: str = "general",
) -> str:
    """Route internal LLM call to configured provider.

    Returns the assistant message content as a string.
    Falls back through: Ollama → direct OpenRouter → Bifrost.

    Args:
        stage: Pipeline stage name (e.g. "claim_extraction", "query_decomposition").
               Used for per-stage circuit breaker isolation when provider is Ollama.
    """
    provider = getattr(config, "INTERNAL_LLM_PROVIDER", "bifrost")

    if provider == "ollama":
        return await _call_ollama(
            messages, temperature=temperature, max_tokens=max_tokens,
            json_mode=response_format is not None and response_format.get("type") == "json_object",
            stage=stage,
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
    json_mode: bool = False,
    stage: str = "general",
) -> str:
    """Call local Ollama instance for internal operations."""
    import httpx

    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    model = getattr(config, "INTERNAL_LLM_MODEL", "") or config.OLLAMA_DEFAULT_MODEL
    # Per-stage circuit breaker isolation: each pipeline stage gets its own
    # breaker so a failing stage (e.g. claim_extraction) doesn't block others.
    breaker_name = f"ollama-{stage}" if stage != "general" else "ollama"
    breaker = get_breaker(breaker_name)

    async def _do_call() -> str:
        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        # Ollama supports format: "json" to enforce JSON output
        if json_mode:
            payload["format"] = "json"

        client = _get_ollama_client()
        resp = await client.post(
            f"{ollama_url}/api/chat",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "")

    try:
        return await breaker.call(_do_call)
    except CircuitOpenError:
        logger.warning("Ollama circuit breaker open — falling back to OpenRouter")
    except httpx.ConnectError:
        logger.warning("Ollama unreachable at %s (is 'ollama serve' running?) — falling back to OpenRouter", ollama_url)
    except httpx.TimeoutException:
        logger.warning("Ollama request timed out (model may be loading or server overloaded) — falling back to OpenRouter")
    except httpx.HTTPStatusError as e:
        logger.warning("Ollama HTTP %d — falling back to OpenRouter", e.response.status_code)

    from utils.llm_client import call_llm
    return await call_llm(
        messages, temperature=temperature, max_tokens=max_tokens,
    )
