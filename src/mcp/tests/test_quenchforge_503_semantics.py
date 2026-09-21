# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A bare 503 from quenchforge is a config error, not back-pressure.

F167: the retry loop treated every 503 as overload and slept a 1.0s default when
no Retry-After was present. Quenchforge only sets Retry-After on its auto-backoff
branch; the missing-slot 503 carries none, so a permanent misconfiguration cost
4 POSTs and 3s of sleeps per rerank — serialized behind a semaphore of 1. And
``raise_for_status()`` discarded the body, so the daemon's own answer
("no rerank slot configured. Check `quenchforge doctor`") never reached /health.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from utils import quenchforge_client as qf

NO_SLOT = "no rerank slot configured. Check `quenchforge doctor` for status."


def _resp(status: int, *, retry_after: str | None = None, json_data: dict | None = None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"Retry-After": retry_after} if retry_after is not None else {}
    resp.json = MagicMock(return_value=json_data if json_data is not None else {})
    resp.text = ""
    if 400 <= status < 600:
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                f"Server error '{status}' for url 'http://qf/v1/rerank'",
                request=MagicMock(),
                response=MagicMock(status_code=status),
            )
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
def _passthrough_breaker():
    breaker = MagicMock()

    async def _call(coro_fn):
        return await coro_fn()

    breaker.call = _call
    return breaker


async def test_bare_503_raises_immediately_without_retrying(
    monkeypatch, _passthrough_breaker
):
    """No Retry-After means the daemon is not asking us to back off."""
    monkeypatch.setenv("QUENCHFORGE_RERANK_MODEL", "bge-reranker-v2-m3")
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp(503, json_data={"error": NO_SLOT}))

    slept: list[float] = []

    async def _spy_sleep(seconds):
        slept.append(seconds)

    with patch.object(qf, "_get_client", AsyncMock(return_value=client)), \
         patch.object(qf, "get_breaker", return_value=_passthrough_breaker), \
         patch.object(qf.asyncio, "sleep", new=_spy_sleep), \
         pytest.raises(httpx.HTTPStatusError):
        await qf.quenchforge_rerank("q", ["doc"])

    assert client.post.await_count == 1, "a config error was retried as overload"
    assert slept == [], "slept on a failure the daemon never asked us to retry"


async def test_daemon_error_body_reaches_the_exception(
    monkeypatch, _passthrough_breaker
):
    """The one string that explains the outage must survive the boundary —
    record_fallback's detail is built from str(exc)."""
    monkeypatch.setenv("QUENCHFORGE_RERANK_MODEL", "bge-reranker-v2-m3")
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp(503, json_data={"error": NO_SLOT}))

    with patch.object(qf, "_get_client", AsyncMock(return_value=client)), \
         patch.object(qf, "get_breaker", return_value=_passthrough_breaker), \
         pytest.raises(httpx.HTTPStatusError) as excinfo:
        await qf.quenchforge_rerank("q", ["doc"])

    assert "no rerank slot configured" in str(excinfo.value)


async def test_503_with_retry_after_still_backs_off(monkeypatch, _passthrough_breaker):
    """Retry-After IS quenchforge's back-pressure signal — honour it."""
    monkeypatch.setenv("QUENCHFORGE_RERANK_MODEL", "bge-reranker-v2-m3")
    client = MagicMock()
    client.post = AsyncMock(side_effect=[
        _resp(503, retry_after="0"),
        _resp(200, json_data={"results": [{"index": 0, "relevance_score": 1.0}]}),
    ])

    with patch.object(qf, "_get_client", AsyncMock(return_value=client)), \
         patch.object(qf, "get_breaker", return_value=_passthrough_breaker):
        scores = await qf.quenchforge_rerank("q", ["doc"])

    assert client.post.await_count == 2
    assert len(scores) == 1


def test_module_docstring_describes_the_breakers_that_exist():
    """F179: the docstring promised one shared 'quenchforge' breaker; the code
    ships three per-workload ones and no breaker by that name exists."""
    doc = qf.__doc__ or ""
    assert "quenchforge-embed" in doc
    assert "quenchforge-rerank" in doc
    assert "shared with the LLM-chat path" not in doc
