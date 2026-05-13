# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Internal LLM call utility — routes to OpenRouter, Ollama, or Quenchforge based on INTERNAL_LLM_PROVIDER.

Used by pipeline operations that need lightweight LLM intelligence:
- Query decomposition
- Claim extraction
- Contextual chunk summaries
- AI categorization (smart tier)
- Memory conflict resolution

Quenchforge speaks the Ollama HTTP protocol identically — provider="quenchforge"
reuses the Ollama wire format but reads QUENCHFORGE_URL instead of OLLAMA_URL.

NOT used for user-facing chat (that goes through /chat/stream → OpenRouter).
NOT used for verification (that uses dedicated VERIFICATION_MODEL).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

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
    Providers: "ollama" (local), "quenchforge" (local Mac+AMD), or
    "openrouter" (default cloud aggregator).

    The *stage* argument is a first-class observability breadcrumb: every
    internal-LLM call is attributed to a named pipeline stage (e.g.
    ``"topic_extraction"``, ``"claim_extraction"``, ``"contextual_summary"``).
    It flows into log records and, when the Sentry SDK is active, into
    the current scope as a tag. Callers are encouraged — but not required —
    to supply it.
    """
    provider = getattr(config, "INTERNAL_LLM_PROVIDER", "openrouter")
    log: logging.Logger | logging.LoggerAdapter = logger
    if stage:
        log = logging.LoggerAdapter(logger, {"llm_stage": stage})
        try:
            import sentry_sdk  # type: ignore[import-not-found]
            sentry_sdk.set_tag("llm_stage", stage)
        except ImportError:
            pass
        log.debug("internal LLM call provider=%s stage=%s", provider, stage)

    if provider in ("ollama", "quenchforge"):
        return await _call_ollama(
            messages,
            provider=provider,
            temperature=temperature,
            max_tokens=max_tokens,
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
    provider: str = "ollama",
) -> str:
    """Call a local Ollama-protocol backend (stock Ollama or Quenchforge).

    The wire format is identical across both backends; ``provider`` only
    selects the URL (``OLLAMA_URL`` vs ``QUENCHFORGE_URL``) and the
    user-facing label in fallback log lines.
    """
    import httpx

    if provider == "quenchforge":
        base_url = getattr(config, "QUENCHFORGE_URL", "") or os.getenv(
            "OLLAMA_URL", "http://localhost:11434"
        )
        label = "Quenchforge"
        start_hint = "is the quenchforge daemon running?"
    else:
        base_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        label = "Ollama"
        start_hint = "is 'ollama serve' running?"
    model = getattr(config, "INTERNAL_LLM_MODEL", "") or config.OLLAMA_DEFAULT_MODEL
    # Breaker key is provider-specific so a Quenchforge outage doesn't trip
    # the Ollama breaker (and vice versa). Pre-v0.93.9 both providers shared
    # the "ollama" breaker; mismatched failures cascaded across an operator
    # who happened to have both daemons running on different ports.
    # quenchforge_client uses the "quenchforge" breaker for /v1/embeddings
    # and /v1/rerank — this internal-LLM /api/chat path matches that key.
    breaker = get_breaker(provider) if provider == "quenchforge" else get_breaker("ollama")

    # Advanced flags (default off). When any is set, additive payload fields
    # are surfaced; the wire stays valid against stock Ollama and Quenchforge.
    options: dict[str, Any] = {
        "temperature": temperature,
        "num_predict": max_tokens,
    }
    if json_mode and getattr(config, "ENABLE_CONSTRAINED_DECODE", False):
        # Constrained decode pairs with json_mode by forcing deterministic
        # output — otherwise the model can still emit valid JSON that varies
        # per sample. Operators wanting freshness override the flag.
        options["temperature"] = 0.0
    if getattr(config, "ENABLE_SPECULATIVE_DECODE", False):
        draft_model = (
            getattr(config, "INTERNAL_LLM_DRAFT_MODEL", "")
            or os.getenv("INTERNAL_LLM_DRAFT_MODEL", "")
        )
        if draft_model:
            options["draft_model"] = draft_model

    async def _do_call() -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": options,
        }
        # Both backends accept format: "json" to enforce JSON output
        if json_mode:
            payload["format"] = "json"
        # Prefix-cache keep-alive: ask the backend to keep the model loaded
        # between calls so prompt-prefix reuse (KV cache hits) survives.
        if getattr(config, "ENABLE_PROMPT_PREFIX_CACHE", False):
            payload["keep_alive"] = getattr(
                config, "PROMPT_PREFIX_KEEP_ALIVE", "30m"
            )

        client = await _get_ollama_client()
        resp = await client.post(
            f"{base_url}/api/chat",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "")

    try:
        return await breaker.call(_do_call)
    except CircuitOpenError:
        logger.warning("%s circuit breaker open — falling back to OpenRouter", label)
    except httpx.ConnectError:
        logger.warning("%s unreachable at %s (%s) — falling back to OpenRouter", label, base_url, start_hint)
    except httpx.TimeoutException:
        logger.warning("%s request timed out (model may be loading or server overloaded) — falling back to OpenRouter", label)
    except httpx.HTTPStatusError as e:
        logger.warning("%s HTTP %d — falling back to OpenRouter", label, e.response.status_code)

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
