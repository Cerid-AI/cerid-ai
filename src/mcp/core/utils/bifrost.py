# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared Bifrost LLM call utility with circuit breaker and tracing.

Provides a shared :class:`httpx.AsyncClient` connection pool so that
multiple LLM calls within the same request (e.g. 10-claim verification)
reuse TCP connections instead of opening a new one per call.
"""

from __future__ import annotations

import logging

import httpx

import config.settings as config
from core.utils.circuit_breaker import get_breaker
from core.utils.tracing import tracing_headers

logger = logging.getLogger("ai-companion.bifrost")

# ---------------------------------------------------------------------------
# Shared httpx connection pool — reused across all Bifrost/LLM calls
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None


def get_bifrost_client() -> httpx.AsyncClient:
    """Get or create the shared httpx client for Bifrost calls.

    Connection pool is sized for concurrent verification workloads
    (up to 20 concurrent connections, 10 keep-alive).
    """
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=15),
            timeout=config.BIFROST_TIMEOUT,
        )
    return _client


async def close_bifrost_client() -> None:
    """Close the shared httpx client.  Call during application shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None


async def call_bifrost(
    messages: list[dict],
    *,
    breaker_name: str,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 2000,
    timeout: float | None = None,
    extra_payload: dict | None = None,
    api_key: str | None = None,
) -> dict:
    """Make a chat/completions call to Bifrost with circuit breaker + tracing.

    Returns the parsed JSON response dict.
    Raises CircuitOpenError if the breaker is open.
    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    breaker = get_breaker(breaker_name)
    effective_timeout = timeout or config.BIFROST_TIMEOUT
    effective_model = model or config.LLM_INTERNAL_MODEL

    payload: dict = {
        "model": effective_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if extra_payload:
        payload.update(extra_payload)

    async def _call() -> dict:
        headers = tracing_headers()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        client = get_bifrost_client()
        resp = await client.post(
            f"{config.BIFROST_URL}/chat/completions",
            json=payload,
            headers=headers,
            timeout=effective_timeout,
        )
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 402:
                _set_credits_exhausted()
            raise
        # On success, clear exhaustion flag if it was previously set
        _clear_credits_exhausted()
        return resp.json()

    return await breaker.call(_call)


_CREDITS_KEY = "cerid:openrouter:credits_exhausted"


def _set_credits_exhausted() -> None:
    """Record that OpenRouter returned 402 (credits exhausted)."""
    try:
        from deps import get_redis

        get_redis().set(_CREDITS_KEY, "1", ex=300)  # 5-min TTL
    except Exception:
        pass


def _clear_credits_exhausted() -> None:
    """Clear the exhaustion flag after a successful Bifrost call."""
    try:
        from deps import get_redis

        r = get_redis()
        if r.exists(_CREDITS_KEY):
            r.delete(_CREDITS_KEY)
    except Exception:
        pass


def extract_content(data: dict) -> str:
    """Extract the text content from a Bifrost chat/completions response."""
    choices = data.get("choices")
    if not choices:
        raise KeyError("No choices in response")
    message = choices[0].get("message") if choices[0] else None
    if not message:
        raise KeyError("No message in response")
    content = message.get("content")
    if content is None:
        raise KeyError("No content in response")
    return content.strip()
