# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""/query must surface budget degradation (2026-07-13).

agent_query returns an empty degraded envelope when the wall-clock budget
expires. The router previously stripped budget_exceeded/degraded_reason, so
a degraded-empty response was byte-identical to a true zero-hit — consumers
(chat auto-inject, SDK, live eval harnesses) could neither retry nor
discount it. Live-proven: the first live-retrieval baseline scored
cross-domain queries 0.0 because degraded-empties read as misses.
"""

# E1 CR-087: query_endpoint now resolves the caller's consumer via the request
# headers; a header-less fake resolves to the default (gui, unrestricted).
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.routers.query import QueryRequest, query_endpoint

_REQ = SimpleNamespace(headers={})

_DEGRADED_RESULT = {
    "context": "",
    "sources": [],
    "confidence": 0.0,
    "budget_exceeded": True,
    "degraded_reason": "Retrieval took longer than the configured budget.",
    "strategy": "degraded_budget_exhausted",
}

_NORMAL_RESULT = {
    "context": "ctx",
    "sources": [{"filename": "doc.md"}],
    "confidence": 0.8,
}


def _patches(result):
    return (
        patch("core.agents.query_agent.agent_query_full",
              new=AsyncMock(return_value=result)),
        patch("app.routers.query.private_blocks", return_value=False),
        patch("app.routers.query.get_chroma", return_value=None),
        patch("app.routers.query.get_redis", return_value=None),
        patch("app.routers.query.get_neo4j", return_value=None),
        patch("app.routers.query.get_graph_store", return_value=None),
    )


@pytest.mark.asyncio
async def test_degraded_envelope_passes_through():
    patches = _patches(_DEGRADED_RESULT)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        resp = await query_endpoint(QueryRequest(query="anything"), _REQ)
    assert resp["budget_exceeded"] is True
    assert "budget" in (resp["degraded_reason"] or "")
    assert resp["sources"] == []


@pytest.mark.asyncio
async def test_normal_result_defaults_not_degraded():
    patches = _patches(_NORMAL_RESULT)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        resp = await query_endpoint(QueryRequest(query="anything"), _REQ)
    assert resp["budget_exceeded"] is False
    assert resp["degraded_reason"] is None
    assert resp["sources"] == [{"filename": "doc.md"}]


@pytest.mark.asyncio
async def test_budget_seconds_reaches_agent_query():
    """/query must ACCEPT and FORWARD budget_seconds (2026-07-14).

    The override existed only on /agent/query; FastAPI silently ignored the
    field on /query (unknown-param class), so eval harnesses opting into a
    60s budget still ran on the 20s interactive default and mis-scored
    load-degraded empties as retrieval misses.
    """
    mock = AsyncMock(return_value=_NORMAL_RESULT)
    patches = _patches(_NORMAL_RESULT)
    with patch("core.agents.query_agent.agent_query_full", new=mock), \
         patches[1], patches[2], patches[3], patches[4], patches[5]:
        await query_endpoint(QueryRequest(query="anything", budget_seconds=60.0), _REQ)
    assert mock.await_args.kwargs.get("budget_seconds") == 60.0


@pytest.mark.asyncio
async def test_budget_seconds_default_is_none():
    mock = AsyncMock(return_value=_NORMAL_RESULT)
    patches = _patches(_NORMAL_RESULT)
    with patch("core.agents.query_agent.agent_query_full", new=mock), \
         patches[1], patches[2], patches[3], patches[4], patches[5]:
        await query_endpoint(QueryRequest(query="anything"), _REQ)
    assert mock.await_args.kwargs.get("budget_seconds") is None


@pytest.mark.asyncio
async def test_skip_cache_reaches_agent_query():
    """/query must ACCEPT and FORWARD skip_cache (2026-07-14).

    Same silently-ignored-param class as budget_seconds: an A/B harness
    sending skip_cache=true still got semantic-cache hits, so the second
    arm measured the first arm's cached results (observed live: two fusion
    modes returned identical per-domain metrics to 3 decimals)."""
    mock = AsyncMock(return_value=_NORMAL_RESULT)
    patches = _patches(_NORMAL_RESULT)
    with patch("core.agents.query_agent.agent_query_full", new=mock), \
         patches[1], patches[2], patches[3], patches[4], patches[5]:
        await query_endpoint(QueryRequest(query="anything", skip_cache=True), _REQ)
    assert mock.await_args.kwargs.get("skip_cache") is True
