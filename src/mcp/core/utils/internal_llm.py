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

import asyncio
import logging
import os

import httpx

import config
from core.utils.circuit_breaker import CircuitOpenError, get_breaker

logger = logging.getLogger("ai-companion.internal_llm")

# Shared connection pool for Ollama calls (avoids per-request TCP handshake)
_ollama_client: httpx.AsyncClient | None = None
_ollama_client_lock = asyncio.Lock()


async def _get_ollama_client() -> httpx.AsyncClient:
    global _ollama_client
    if _ollama_client is not None and not _ollama_client.is_closed:
        return _ollama_client
    async with _ollama_client_lock:
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
    stage: str | None = None,
) -> str:
    """Route internal LLM call to configured provider.

    Returns the assistant message content as a string.
    Providers: "ollama" (local) or "openrouter" (default).

    The *stage* argument is a first-class observability breadcrumb: every
    internal-LLM call is attributed to a named pipeline stage (e.g.
    ``"topic_extraction"``, ``"claim_extraction"``, ``"contextual_summary"``).
    It flows into log records and, when the Sentry SDK is active, into
    the current scope as a tag. Callers are encouraged — but not required —
    to supply it.
    """
    provider = getattr(config, "INTERNAL_LLM_PROVIDER", "openrouter")
    log = logger
    if stage:
        log = logging.LoggerAdapter(logger, {"llm_stage": stage})
        try:
            import sentry_sdk  # type: ignore[import-not-found]
            sentry_sdk.set_tag("llm_stage", stage)
        except ImportError:
            pass
        log.debug("internal LLM call provider=%s stage=%s", provider, stage)

    if provider == "ollama":
        return await _call_ollama(
            messages, temperature=temperature, max_tokens=max_tokens,
            json_mode=response_format is not None and response_format.get("type") == "json_object",
        )
    else:
        # Default: direct OpenRouter via unified client
        from core.utils.llm_client import call_llm
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
) -> str:
    """Call local Ollama instance for internal operations."""
    import httpx

    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    model = getattr(config, "INTERNAL_LLM_MODEL", "") or config.OLLAMA_DEFAULT_MODEL
    breaker = get_breaker("ollama")

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

        client = await _get_ollama_client()
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

    # Explicitly use a known-valid OpenRouter model for fallback —
    # INTERNAL_LLM_MODEL may hold an Ollama-native name (e.g. "llama3.2:3b")
    # that OpenRouter rejects with 400.  Also forward json_mode so callers
    # like memory extraction that request structured JSON get it on fallback.
    from core.utils.llm_client import call_llm
    return await call_llm(
        messages,
        model="openai/gpt-4o-mini",
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"} if json_mode else None,
    )
