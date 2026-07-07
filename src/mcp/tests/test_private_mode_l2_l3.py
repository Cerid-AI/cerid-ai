# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Server-side Private Mode L2 (skip KB) and L3 (skip audit) enforcement
(Task 1.2a). Extends ``test_private_mode_enforcement.py`` (L1).

``app/routers/chat.py`` is a pure streaming proxy to OpenRouter — KB context
is injected client-side by ``use-chat-send.ts`` and it performs no
server-side retrieval, so there is nothing to gate there. Server-side
retrieval only happens at:

  * ``POST /agent/query``  (agents.py::agent_query_endpoint -> agent_query_full)
  * ``POST /query``        (query.py::query_endpoint -> agent_query_full)

L3 gates the unconditional MCP tool-call audit emit in
``app.tools.execute_tool``.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.private_mode import PRIVATE_MODE_KEY


class _FakeRedis:
    """Minimal in-memory stand-in — matches test_private_mode_enforcement.py."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value


@pytest.fixture()
def fake_redis():
    return _FakeRedis()


# ---------------------------------------------------------------------------
# agents.py — agent_query_endpoint (/agent/query)
# ---------------------------------------------------------------------------


@pytest.fixture()
def agent_query_client(fake_redis, monkeypatch):
    from app.routers import agents

    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fake_redis)
    # Retrieval kwargs (chroma_client=get_chroma(), ...) are evaluated
    # eagerly by the handler even when agent_query_full is mocked — stub
    # these so the L0 pass-through path doesn't attempt a real connection.
    monkeypatch.setattr(agents, "get_chroma", lambda: object())
    monkeypatch.setattr(agents, "get_neo4j", lambda: object())
    monkeypatch.setattr(agents, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(agents, "get_graph_store", lambda: object())

    app = FastAPI()
    app.include_router(agents.router)
    return TestClient(app, raise_server_exceptions=False)


class TestAgentQueryL2:
    def test_l2_bypasses_kb_and_skips_retrieval(self, agent_query_client, fake_redis):
        fake_redis.set(PRIVATE_MODE_KEY, "2")
        with patch(
            "core.agents.query_agent.agent_query_full", new_callable=AsyncMock
        ) as mock_retrieve:
            res = agent_query_client.post("/agent/query", json={"query": "hello"})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["context"] == ""
        assert body["sources"] == []
        assert body["results"] == []
        assert body["domains_searched"] == []
        assert body["total_results"] == 0
        assert body["confidence"] == 0.0
        assert body["kb_bypassed"] is True
        mock_retrieve.assert_not_called()

    def test_l0_calls_retrieval_normally(self, agent_query_client, fake_redis):
        stub_result = {
            "context": "some context",
            "sources": [{"id": "s1"}],
            "results": [{"id": "s1", "relevance": 0.9}],
            "domains_searched": ["general"],
            "total_results": 1,
            "confidence": 0.9,
        }
        with patch(
            "core.agents.query_agent.agent_query_full",
            new_callable=AsyncMock,
            return_value=stub_result,
        ) as mock_retrieve:
            # skip_cache=True sidesteps utils.query_cache (a real-Redis
            # dependency unrelated to this gate) so the test stays isolated
            # to the private-mode seam.
            res = agent_query_client.post(
                "/agent/query", json={"query": "hello", "skip_cache": True}
            )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["total_results"] == 1
        assert body.get("kb_bypassed") is not True
        mock_retrieve.assert_called_once()


# ---------------------------------------------------------------------------
# query.py — query_endpoint (/query)
# ---------------------------------------------------------------------------


@pytest.fixture()
def query_endpoint_client(fake_redis, monkeypatch):
    from app.routers import query as query_router

    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fake_redis)
    monkeypatch.setattr(query_router, "get_chroma", lambda: object())
    monkeypatch.setattr(query_router, "get_neo4j", lambda: object())
    monkeypatch.setattr(query_router, "get_redis", lambda: fake_redis)
    monkeypatch.setattr(query_router, "get_graph_store", lambda: object())

    app = FastAPI()
    app.include_router(query_router.router)
    return TestClient(app, raise_server_exceptions=False)


class TestQueryEndpointL2:
    def test_l2_bypasses_kb_and_skips_retrieval(self, query_endpoint_client, fake_redis):
        fake_redis.set(PRIVATE_MODE_KEY, "2")
        with patch(
            "core.agents.query_agent.agent_query_full", new_callable=AsyncMock
        ) as mock_retrieve:
            res = query_endpoint_client.post("/query", json={"query": "hello"})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["context"] == ""
        assert body["sources"] == []
        assert body["confidence"] == 0.0
        assert "timestamp" in body
        mock_retrieve.assert_not_called()

    def test_l0_calls_retrieval_normally(self, query_endpoint_client, fake_redis):
        stub_result = {"context": "ctx", "sources": [{"id": "s1"}], "confidence": 0.5}
        with patch(
            "core.agents.query_agent.agent_query_full",
            new_callable=AsyncMock,
            return_value=stub_result,
        ) as mock_retrieve:
            res = query_endpoint_client.post("/query", json={"query": "hello"})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["context"] == "ctx"
        assert body["confidence"] == 0.5
        mock_retrieve.assert_called_once()


# ---------------------------------------------------------------------------
# tools.py — execute_tool audit emit (L3, "skip audit")
# ---------------------------------------------------------------------------


class TestMcpToolCallAuditL3:
    """Gate the ``mcp.tool_call`` audit-log emit in execute_tool's finally.

    Invoked at the closest feasible seam: ``execute_tool("pkb_health", {})``
    with ``app.tools.health_check`` patched — this is the same pattern
    ``test_tools.py::TestExecuteToolSync::test_pkb_health`` already uses to
    invoke the dispatch wrapper in isolation.
    """

    def test_l3_skips_audit_emit(self, fake_redis, monkeypatch, caplog):
        monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fake_redis)
        fake_redis.set(PRIVATE_MODE_KEY, "3")
        from app.tools import execute_tool

        with patch("app.tools.health_check", return_value={"status": "healthy"}):
            with caplog.at_level(logging.INFO, logger="ai-companion.mcp_tool_audit"):
                asyncio.run(execute_tool("pkb_health", {}))

        assert not any(
            r.getMessage() == "mcp.tool_call" for r in caplog.records
        )

    def test_l0_emits_audit(self, fake_redis, monkeypatch, caplog):
        monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fake_redis)
        from app.tools import execute_tool

        with patch("app.tools.health_check", return_value={"status": "healthy"}):
            with caplog.at_level(logging.INFO, logger="ai-companion.mcp_tool_audit"):
                asyncio.run(execute_tool("pkb_health", {}))

        assert any(
            r.getMessage() == "mcp.tool_call" for r in caplog.records
        )
