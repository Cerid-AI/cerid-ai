# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared Bifrost LLM call utility with circuit breaker and tracing."""

from __future__ import annotations

import logging

import httpx

import config.settings as config
from middleware.request_id import tracing_headers
from utils.circuit_breaker import get_breaker

logger = logging.getLogger("ai-companion.bifrost")


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
        async with httpx.AsyncClient(
            timeout=effective_timeout, headers=headers
        ) as client:
            resp = await client.post(
                f"{config.BIFROST_URL}/chat/completions",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    return await breaker.call(_call)


def extract_content(data: dict) -> str:
    """Extract the text content from a Bifrost chat/completions response."""
    return data["choices"][0]["message"]["content"].strip()
