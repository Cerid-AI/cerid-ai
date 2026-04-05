# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""OpenRouter/Bifrost implementation of LLMClient contract."""

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
