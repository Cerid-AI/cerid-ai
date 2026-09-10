# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Regression: ``call_llm`` maps a 402 upstream response to
``CreditExhaustedError`` exactly like ``call_llm_raw`` does (Task C1).

Before this fix ``call_llm`` re-raised a 402 as a bare
``httpx.HTTPStatusError`` while ``call_llm_raw`` mapped it to
``CreditExhaustedError`` — callers that only knew about the credits-exhausted
error type (e.g. verification) silently mis-handled the chat path's 402.
"""
from __future__ import annotations

from typing import Any

import pytest

from core.agents.hallucination.verification import CreditExhaustedError
from core.utils import llm_client

pytestmark = pytest.mark.asyncio


class _FixedStatusResponse:
    def __init__(self, status_code: int, body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}

    def raise_for_status(self) -> None:
        raise AssertionError("raise_for_status must not be reached for a 402 response")

    def json(self) -> dict[str, Any]:
        return self._body


class _FixedStatusClient:
    def __init__(self, response: _FixedStatusResponse) -> None:
        self._response = response

    async def post(self, _url: str, **_kwargs: Any) -> _FixedStatusResponse:
        return self._response

    @property
    def is_closed(self) -> bool:
        return False

    async def aclose(self) -> None:
        return None


class _FixedStatusClientCtx:
    def __init__(self, client: _FixedStatusClient) -> None:
        self._client = client

    async def __aenter__(self) -> _FixedStatusClient:
        return self._client

    async def __aexit__(self, *_: Any) -> None:
        return None


def _install_402_client(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"error": {"code": 402, "message": "Insufficient credits"}}
    response = _FixedStatusResponse(402, body)
    client = _FixedStatusClient(response)
    monkeypatch.setattr(llm_client, "_acquire_client", lambda: _FixedStatusClientCtx(client))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")


async def test_call_llm_maps_402_to_credit_exhausted_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_402_client(monkeypatch)

    with pytest.raises(CreditExhaustedError):
        await llm_client.call_llm([{"role": "user", "content": "hi"}], model="x/y")


async def test_call_llm_raw_maps_402_to_credit_exhausted_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin for the existing call_llm_raw behavior call_llm now matches."""
    _install_402_client(monkeypatch)

    with pytest.raises(CreditExhaustedError):
        await llm_client.call_llm_raw([{"role": "user", "content": "hi"}], model="x/y")
