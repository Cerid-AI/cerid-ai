# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""POST /agent/query must drop KB_POOL when the client disconnects.

Without this, a cancelled TanStack query leaves wait_for(20s) running and
a second duplicate POST from the sibling hook occupies the other slots.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _request(*, disconnected: bool | list[bool]) -> MagicMock:
    request = MagicMock()
    request.headers = {"x-client-id": "gui"}
    if isinstance(disconnected, list):
        request.is_disconnected = AsyncMock(side_effect=disconnected)
    else:
        request.is_disconnected = AsyncMock(return_value=disconnected)
    return request


@pytest.mark.asyncio
async def test_inner_returns_empty_when_disconnected():
    from app.routers import agents
    from app.routers.agents import AgentQueryRequest

    req = AgentQueryRequest(query="hello world", skip_cache=True)
    request = _request(disconnected=True)

    # Manual rag_mode (default) dispatches to agent_query_full, not agent_query.
    with (
        patch.object(agents, "get_chroma", return_value=MagicMock()),
        patch.object(agents, "get_neo4j", return_value=MagicMock()),
        patch.object(agents, "get_graph_store", return_value=MagicMock()),
        patch.object(agents, "get_redis", return_value=MagicMock()),
        patch("core.agents.query_agent.agent_query_full", new=AsyncMock()) as spy,
    ):
        result = await agents._agent_query_inner(req, request)
    spy.assert_not_called()
    assert result.get("results") == [] or result.get("total_results") == 0


@pytest.mark.asyncio
async def test_inner_skips_heavy_call_when_disconnect_after_cache_miss():
    from app.routers import agents
    from app.routers.agents import AgentQueryRequest

    req = AgentQueryRequest(query="hello world", skip_cache=False)
    request = _request(disconnected=[False, True])

    with (
        patch.object(agents, "get_chroma", return_value=MagicMock()),
        patch.object(agents, "get_neo4j", return_value=MagicMock()),
        patch.object(agents, "get_graph_store", return_value=MagicMock()),
        patch.object(agents, "get_redis", return_value=MagicMock()),
        patch("utils.query_cache.get_cached", return_value=None),
        patch("core.agents.query_agent.agent_query_full", new=AsyncMock()) as spy,
    ):
        result = await agents._agent_query_inner(req, request)
    spy.assert_not_called()
    assert result.get("results") == [] or result.get("total_results") == 0


@pytest.mark.asyncio
async def test_cancelled_error_is_not_swallowed_as_500():
    from app.routers import agents
    from app.routers.agents import AgentQueryRequest

    req = AgentQueryRequest(query="hello world", skip_cache=True)
    request = _request(disconnected=False)

    async def boom(**_kw):
        raise asyncio.CancelledError()

    with (
        patch.object(agents, "get_chroma", return_value=MagicMock()),
        patch.object(agents, "get_neo4j", return_value=MagicMock()),
        patch.object(agents, "get_graph_store", return_value=MagicMock()),
        patch.object(agents, "get_redis", return_value=MagicMock()),
        patch("core.agents.query_agent.agent_query_full", new=boom),
    ):
        with pytest.raises(asyncio.CancelledError):
            await agents._agent_query_inner(req, request)


@pytest.mark.asyncio
async def test_cancelled_query_releases_kb_pool():
    from app.concurrency import KB_POOL
    from app.routers import agents
    from app.routers.agents import AgentQueryRequest

    started = asyncio.Event()

    async def hang(**_kw):
        started.set()
        await asyncio.sleep(30)
        return {"results": []}

    req = AgentQueryRequest(query="hello world", skip_cache=True)
    request = _request(disconnected=False)

    in_use_before, waiting_before = KB_POOL.queue_depth()
    with (
        patch.object(agents, "get_chroma", return_value=MagicMock()),
        patch.object(agents, "get_neo4j", return_value=MagicMock()),
        patch.object(agents, "get_graph_store", return_value=MagicMock()),
        patch.object(agents, "get_redis", return_value=MagicMock()),
        patch("core.agents.query_agent.agent_query_full", new=hang),
    ):
        task = asyncio.create_task(agents.agent_query_endpoint(req, request))
        await started.wait()
        assert KB_POOL.queue_depth()[0] >= in_use_before + 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert KB_POOL.queue_depth() == (in_use_before, waiting_before)
