# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The retrieval seed waits for the rate-limit reset the 429 announces.

Offline: an httpx MockTransport stands in for the server and asyncio.sleep is
recorded, so nothing waits for real.
"""
from __future__ import annotations

import asyncio

import conftest as eval_conftest
import httpx
import pytest


def _client(responses: list[httpx.Response]) -> httpx.AsyncClient:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return queue.pop(0)

    return httpx.AsyncClient(base_url="http://mcp", transport=httpx.MockTransport(handler))


def _limited(reset: str | None) -> httpx.Response:
    headers = {} if reset is None else {"Retry-After": reset, "RateLimit-Reset": reset}
    return httpx.Response(429, headers=headers, json={"detail": "Rate limit exceeded."})


def test_a_429_waits_for_the_announced_reset_and_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)

    monkeypatch.setattr(eval_conftest.asyncio, "sleep", fake_sleep)

    async def run() -> str:
        async with _client([_limited("42"), httpx.Response(200, json={"artifact_id": "a1"})]) as c:
            return await eval_conftest.seed_content(c, "text")

    assert asyncio.run(run()) == "a1"
    assert slept == [42.0]


def test_a_window_longer_than_the_old_backoff_budget_is_waited_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """The old helper slept 3+6+9+12 = 30 s in total and then raised, which a
    60 s window outlasts; four announced 20 s waits now end in a success."""
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)

    monkeypatch.setattr(eval_conftest.asyncio, "sleep", fake_sleep)
    responses = [_limited("20") for _ in range(4)] + [httpx.Response(200, json={"artifact_id": "a2"})]

    async def run() -> str:
        async with _client(responses) as c:
            return await eval_conftest.seed_content(c, "text")

    assert asyncio.run(run()) == "a2"
    assert sum(slept) == 80.0


def test_the_wait_is_capped_and_a_missing_header_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    assert eval_conftest._retry_after_s(_limited("600"), 0) == eval_conftest._SEED_MAX_WAIT_S
    assert eval_conftest._retry_after_s(_limited(None), 1) == 6.0
    assert eval_conftest._retry_after_s(_limited("soon"), 0) == 3.0
