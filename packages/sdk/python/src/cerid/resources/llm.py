# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""LLM resource: smart-routed completion."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from cerid.errors import _raise_for_status
from cerid.models import LLMCompleteResponse

if TYPE_CHECKING:
    import httpx

    from cerid._base import _BaseClient


class _LLMResourceBase:
    """Shared bits between sync + async — kept narrow on purpose so the
    transport-flavored subclasses below stay tight."""

    def _body(
        self,
        *,
        messages: List[Dict[str, str]],
        task_type: str,
        query: str,
        cost_sensitivity: str,
        temperature: float,
        max_tokens: int,
        response_format: Optional[Dict[str, Any]],
        slo_budget_ms: Optional[int],
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "messages": messages,
            "task_type": task_type,
            "query": query,
            "cost_sensitivity": cost_sensitivity,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            body["response_format"] = response_format
        if slo_budget_ms is not None:
            body["slo_budget_ms"] = slo_budget_ms
        return body


class LLMResource(_LLMResourceBase):
    """Synchronous smart-routed LLM completion.

    The server's ``smart_router`` selects a model tier (FREE / CHEAP /
    CAPABLE / RESEARCH / EXPERT) based on ``task_type``, ``query`` complexity,
    and ``cost_sensitivity``. When ``slo_budget_ms`` is set, tiers whose
    empirical p95 exceeds the budget are filtered out — if no tier fits the
    response is HTTP 503 with a ``Retry-After`` header carrying the floor
    p95.
    """

    def __init__(self, client: _BaseClient, http: httpx.Client) -> None:
        self._client = client
        self._http = http

    def complete(
        self,
        messages: List[Dict[str, str]],
        *,
        task_type: str = "internal",
        query: str = "",
        cost_sensitivity: str = "medium",
        temperature: float = 0.1,
        max_tokens: int = 500,
        response_format: Optional[Dict[str, Any]] = None,
        slo_budget_ms: Optional[int] = None,
    ) -> LLMCompleteResponse:
        body = self._body(
            messages=messages,
            task_type=task_type,
            query=query,
            cost_sensitivity=cost_sensitivity,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            slo_budget_ms=slo_budget_ms,
        )
        resp = self._http.post(self._client._url("/llm/complete"), json=body)
        _raise_for_status(resp)
        return LLMCompleteResponse.model_validate(resp.json())


class AsyncLLMResource(_LLMResourceBase):
    """Asynchronous smart-routed LLM completion."""

    def __init__(self, client: _BaseClient, http: httpx.AsyncClient) -> None:
        self._client = client
        self._http = http

    async def complete(
        self,
        messages: List[Dict[str, str]],
        *,
        task_type: str = "internal",
        query: str = "",
        cost_sensitivity: str = "medium",
        temperature: float = 0.1,
        max_tokens: int = 500,
        response_format: Optional[Dict[str, Any]] = None,
        slo_budget_ms: Optional[int] = None,
    ) -> LLMCompleteResponse:
        body = self._body(
            messages=messages,
            task_type=task_type,
            query=query,
            cost_sensitivity=cost_sensitivity,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            slo_budget_ms=slo_budget_ms,
        )
        resp = await self._http.post(self._client._url("/llm/complete"), json=body)
        _raise_for_status(resp)
        return LLMCompleteResponse.model_validate(resp.json())
