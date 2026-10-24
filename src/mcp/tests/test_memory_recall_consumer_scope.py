# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Memory recall holds the consumer wall the query path holds (audit F21).

Measured on the live stack 2026-10-10: ``/sdk/v1/memory/recall`` returned the
owner's conversation memories to ``cerid-finance`` and to an unregistered
client, while ``/sdk/v1/query`` kept both out of memory. Only the store read
is faked here; the guard, the policy resolution and the routes are real.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.agents import router as agents_router
from app.routers.sdk import router as sdk_router
from core.agents.guarded_retrieval import guarded_recall_memories
from core.agents.request_context import ConsumerScopeRestricted, RequestContext

MEMORY = {"memory_id": "m1", "text": "the owner's decision", "adjusted_score": 0.9}
ROUTES = ["/sdk/v1/memory/recall", "/agent/memory/recall"]


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(sdk_router)
    app.include_router(agents_router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _private_mode_off(monkeypatch):
    monkeypatch.setattr("app.services.request_policy.get_private_mode_level", lambda: 0)


@pytest.fixture
def store():
    with (
        patch("app.deps.get_chroma", lambda: MagicMock()),
        patch("app.deps.get_neo4j", lambda: MagicMock()),
        patch("app.routers.agents.get_chroma", lambda: MagicMock()),
        patch("app.routers.agents.get_neo4j", lambda: MagicMock()),
        patch("core.agents.memory.recall_memories", new=AsyncMock(return_value=[MEMORY])) as read,
    ):
        yield read


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("client_id", ["cerid-finance", "some-unregistered-client"])
def test_a_strict_consumer_without_conversations_is_refused(client, store, route, client_id):
    resp = client.post(route, json={"query": "what did we decide", "top_k": 3}, headers={"X-Client-ID": client_id})

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["retrieval_reason"] == "consumer_domain_restricted"
    store.assert_not_awaited()


@pytest.mark.parametrize("route", ROUTES)
def test_the_owner_still_recalls(client, store, route):
    resp = client.post(route, json={"query": "what did we decide", "top_k": 3}, headers={"X-Client-ID": "gui"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["memories"][0]["memory_id"] == "m1"


def test_the_agent_route_reports_a_failed_recall_as_degraded(client, store):
    store.side_effect = RuntimeError("chroma down")

    resp = client.post("/agent/memory/recall", json={"query": "q"}, headers={"X-Client-ID": "gui"})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"memories": [], "total": 0, "degraded": True}


@pytest.mark.asyncio
async def test_a_strict_scope_that_names_conversations_reaches_memory(store):
    ctx = RequestContext(client_id="c", allowed_domains=("finance", "conversations"), strict_domains=True)

    assert await guarded_recall_memories(request_context=ctx, query="q") == [MEMORY]


@pytest.mark.asyncio
async def test_a_non_strict_allow_list_keeps_memory_as_the_query_path_does(store):
    ctx = RequestContext(client_id="c", allowed_domains=("finance",), strict_domains=False)

    assert await guarded_recall_memories(request_context=ctx, query="q") == [MEMORY]


@pytest.mark.asyncio
async def test_the_guard_refuses_before_the_store_is_read(store):
    ctx = RequestContext(client_id="c", allowed_domains=("finance",), strict_domains=True)

    with pytest.raises(ConsumerScopeRestricted):
        await guarded_recall_memories(request_context=ctx, query="q")
    store.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_mcp_recall_tool_honours_private_mode(monkeypatch, store):
    from app.tools import execute_tool

    monkeypatch.setattr("app.services.request_policy.get_private_mode_level", lambda: 2)
    with patch("app.tools.get_chroma", lambda: MagicMock(), create=True), patch(
        "app.tools.get_neo4j", lambda: MagicMock(), create=True
    ):
        out = await execute_tool("pkb_memory_recall", {"query": "q"})

    assert out["memories"] == []
    store.assert_not_awaited()
