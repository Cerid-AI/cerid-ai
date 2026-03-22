# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unified LLM client — calls OpenRouter directly, bypassing Bifrost.

All internal LLM operations route through this client.  Bifrost is optional
and only used as a fallback when OPENROUTER_API_KEY is not set or when the
OpenRouter circuit breaker is open.

Usage::

    from utils.llm_client import call_llm

    answer = await call_llm(
        [{"role": "user", "content": "Summarize this text..."}],
        model="openrouter/openai/gpt-4o-mini",
        breaker_name="bifrost-verify",
    )
"""

from __future__ import annotations

import logging
import os

import httpx

from middleware.request_id import tracing_headers
from utils.circuit_breaker import CircuitOpenError, get_breaker

_logger = logging.getLogger("ai-companion.llm_client")

# ---------------------------------------------------------------------------
# Singleton connection pool for OpenRouter
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Get or create the shared httpx client for direct OpenRouter calls.

    Connection pool is sized for concurrent verification workloads
    (up to 20 concurrent connections, 10 keep-alive).
    """
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            timeout=httpx.Timeout(connect=10, read=60, write=10, pool=10),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def close_client() -> None:
    """Close the shared httpx client.  Call during application shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None


def _strip_openrouter_prefix(model: str) -> str:
    """Strip the ``openrouter/`` prefix from model IDs.

    Settings store model IDs with this prefix for Bifrost compatibility
    (e.g. ``openrouter/openai/gpt-4o-mini``).  OpenRouter's API expects
    the bare ID (``openai/gpt-4o-mini``).
    """
    if model.startswith("openrouter/"):
        return model[len("openrouter/"):]
    return model


async def call_llm(
    messages: list[dict[str, str]],
    *,
    model: str = "",
    temperature: float = 0.1,
    max_tokens: int = 500,
    timeout: float | None = None,
    response_format: dict | None = None,
    extra_payload: dict | None = None,
    breaker_name: str = "openrouter",
) -> str:
    """Call an LLM via OpenRouter directly.  Returns assistant content as string.

    Falls back to Bifrost if ``OPENROUTER_API_KEY`` is not set or the
    OpenRouter circuit breaker is open.

    Parameters
    ----------
    messages:
        Chat messages (OpenAI-compatible format).
    model:
        Model ID (with or without ``openrouter/`` prefix).  When empty,
        uses ``INTERNAL_LLM_MODEL`` from settings or Llama 3.3 70B.
    temperature:
        Sampling temperature.
    max_tokens:
        Maximum response tokens.
    timeout:
        Per-request timeout override (seconds).  ``None`` = client default.
    response_format:
        Optional ``{"type": "json_object"}`` for structured output.
    extra_payload:
        Additional keys merged into the API payload.
    breaker_name:
        Circuit breaker name for this call category.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        # No OpenRouter key — fall back to Bifrost
        return await _bifrost_fallback(
            messages,
            breaker_name=breaker_name,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            response_format=response_format,
            extra_payload=extra_payload,
        )

    if not model:
        model = os.getenv("INTERNAL_LLM_MODEL", "") or "meta-llama/llama-3.3-70b-instruct"

    model = _strip_openrouter_prefix(model)

    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format
    if extra_payload:
        payload.update(extra_payload)

    breaker = get_breaker(breaker_name)

    async def _do_call() -> str:
        client = _get_client()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        # Merge tracing headers for observability
        headers.update(tracing_headers())

        post_kwargs: dict = {"headers": headers, "json": payload}
        if timeout is not None:
            post_kwargs["timeout"] = timeout

        resp = await client.post("/chat/completions", **post_kwargs)
        resp.raise_for_status()
        data = resp.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "")

    try:
        return await breaker.call(_do_call)
    except CircuitOpenError:
        _logger.warning("Circuit '%s' open, falling back to Bifrost", breaker_name)
        return await _bifrost_fallback(
            messages,
            breaker_name=breaker_name,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            response_format=response_format,
            extra_payload=extra_payload,
        )


async def call_llm_raw(
    messages: list[dict[str, str]],
    *,
    model: str = "",
    temperature: float = 0.1,
    max_tokens: int = 500,
    timeout: float | None = None,
    response_format: dict | None = None,
    extra_payload: dict | None = None,
    breaker_name: str = "openrouter",
) -> dict:
    """Like :func:`call_llm` but returns the full parsed response dict.

    Used by verification which needs access to annotations (source URLs)
    and the raw message object, not just the text content.
    """
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return await _bifrost_fallback_raw(
            messages,
            breaker_name=breaker_name,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            response_format=response_format,
            extra_payload=extra_payload,
        )

    if not model:
        model = os.getenv("INTERNAL_LLM_MODEL", "") or "meta-llama/llama-3.3-70b-instruct"

    model = _strip_openrouter_prefix(model)

    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format
    if extra_payload:
        payload.update(extra_payload)

    breaker = get_breaker(breaker_name)

    async def _do_call() -> dict:
        client = _get_client()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        headers.update(tracing_headers())

        post_kwargs: dict = {"headers": headers, "json": payload}
        if timeout is not None:
            post_kwargs["timeout"] = timeout

        resp = await client.post("/chat/completions", **post_kwargs)
        # 402 = credits exhausted — propagate as-is
        if resp.status_code == 402:
            from agents.hallucination.verification import CreditExhaustedError
            raise CreditExhaustedError("openrouter")
        resp.raise_for_status()
        return resp.json()

    try:
        return await breaker.call(_do_call)
    except CircuitOpenError:
        _logger.warning("Circuit '%s' open, falling back to Bifrost", breaker_name)
        return await _bifrost_fallback_raw(
            messages,
            breaker_name=breaker_name,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            response_format=response_format,
            extra_payload=extra_payload,
        )


# ---------------------------------------------------------------------------
# Bifrost fallback (when OpenRouter key is absent or circuit is open)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Smart-routed LLM call (uses smart_router to pick model + provider)
# ---------------------------------------------------------------------------


async def route_and_call(
    messages: list[dict[str, str]],
    *,
    query: str = "",
    task_type: str = "internal",  # "chat", "internal", "verification", etc.
    temperature: float = 0.1,
    max_tokens: int = 500,
    response_format: dict | None = None,
) -> tuple[str, "RouteDecision"]:
    """Smart-route a query to the best LLM, then call it.

    Returns ``(content, route_decision)`` tuple.
    """
    from utils.smart_router import RouteDecision, TaskType, route

    task = TaskType(task_type)
    decision = await route(query, task_type=task)

    if decision.provider == "ollama":
        # Call Ollama directly
        content = await _call_ollama_direct(
            messages,
            model=decision.model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return content, decision
    else:
        # Call OpenRouter
        content = await call_llm(
            messages,
            model=decision.model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        return content, decision


async def _call_ollama_direct(
    messages: list[dict[str, str]],
    *,
    model: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Direct Ollama call for smart-routed queries."""
    import httpx as _httpx

    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    try:
        async with _httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": temperature, "num_predict": max_tokens},
                },
            )
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "")
    except Exception as e:
        _logger.warning("Ollama call failed (%s), falling back to OpenRouter", e)
        return await call_llm(messages, temperature=temperature, max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# Bifrost fallback (when OpenRouter key is absent or circuit is open)
# ---------------------------------------------------------------------------


async def _bifrost_fallback(
    messages: list[dict],
    *,
    breaker_name: str,
    model: str = "",
    temperature: float = 0.1,
    max_tokens: int = 500,
    timeout: float | None = None,
    response_format: dict | None = None,
    extra_payload: dict | None = None,
) -> str:
    from utils.bifrost import call_bifrost, extract_content

    extra: dict = {}
    if response_format:
        extra["response_format"] = response_format
    if extra_payload:
        extra.update(extra_payload)

    data = await call_bifrost(
        messages=messages,
        breaker_name=breaker_name,
        model=model or None,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        extra_payload=extra if extra else None,
    )
    return extract_content(data)


async def _bifrost_fallback_raw(
    messages: list[dict],
    *,
    breaker_name: str,
    model: str = "",
    temperature: float = 0.1,
    max_tokens: int = 500,
    timeout: float | None = None,
    response_format: dict | None = None,
    extra_payload: dict | None = None,
) -> dict:
    from utils.bifrost import call_bifrost

    extra: dict = {}
    if response_format:
        extra["response_format"] = response_format
    if extra_payload:
        extra.update(extra_payload)

    return await call_bifrost(
        messages=messages,
        breaker_name=breaker_name,
        model=model or None,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        extra_payload=extra if extra else None,
    )
