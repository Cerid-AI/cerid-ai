# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Server-side Private Mode L2 (skip KB) and L3 (skip audit) enforcement
(Task 1.2a). Extends ``test_private_mode_enforcement.py`` (L1).

Server-side retrieval L2 gates live at:

  * ``POST /agent/query``  (agents.py::agent_query_endpoint -> agent_query_full)
  * ``POST /query``        (query.py::query_endpoint -> agent_query_full)

``app/routers/chat.py`` and ``POST /sdk/v1/llm/complete`` are NOT exempt (the
prior belief that "chat.py does no server-side retrieval so there is nothing
to gate" is the mental-model error that left a hole): they forward the
caller's PRE-ASSEMBLED ``messages`` verbatim to the provider, so a direct
API/SDK caller that replicates the web client's KB/memory injection reaches
the model with the user's knowledge base despite Private Mode. The gate there
is a payload strip (``strip_injected_context``), not a retrieval skip — see
``TestGenerationBoundaryStrip`` below.

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


# ---------------------------------------------------------------------------
# private_mode.strip_injected_context — the generation-boundary gate (unit)
# ---------------------------------------------------------------------------


class _Msg:
    """Attribute-access message, like chat.py's ``_ChatMessage``."""

    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


class TestStripInjectedContextUnit:
    """The pure helper: L2+ drops marker-bearing system messages; below L2 it
    is a no-op. Covers both message shapes (attr-access + dict)."""

    def _strip(self, fake_redis, monkeypatch, level, messages):
        monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fake_redis)
        if level:
            fake_redis.set(PRIVATE_MODE_KEY, str(level))
        from app.services.private_mode import strip_injected_context

        return strip_injected_context(messages)

    def test_l0_passthrough(self, fake_redis, monkeypatch):
        msgs = [_Msg("system", "<document>secret</document>"), _Msg("user", "hi")]
        out = self._strip(fake_redis, monkeypatch, 0, msgs)
        assert out is msgs  # unchanged identity below L2

    def test_l1_passthrough(self, fake_redis, monkeypatch):
        msgs = [_Msg("system", "<memory>x</memory>"), _Msg("user", "hi")]
        out = self._strip(fake_redis, monkeypatch, 1, msgs)
        assert out is msgs

    def test_l2_strips_document(self, fake_redis, monkeypatch):
        msgs = [_Msg("system", "The user has a personal knowledge base.\n<document id='1'>x</document>"),
                _Msg("user", "what did I write?")]
        out = self._strip(fake_redis, monkeypatch, 2, msgs)
        assert [m.role for m in out] == ["user"]

    def test_l2_strips_memory_and_remembered_context(self, fake_redis, monkeypatch):
        msgs = [_Msg("system", "[Remembered Context]\n<memory type='preference'>y</memory>"),
                _Msg("user", "hi")]
        out = self._strip(fake_redis, monkeypatch, 2, msgs)
        assert [m.role for m in out] == ["user"]

    def test_l2_preserves_plain_system_instruction(self, fake_redis, monkeypatch):
        msgs = [_Msg("system", "You are a helpful assistant."), _Msg("user", "hi")]
        out = self._strip(fake_redis, monkeypatch, 2, msgs)
        assert len(out) == 2  # no injection markers -> not stripped

    def test_l3_strips_like_l2(self, fake_redis, monkeypatch):
        msgs = [_Msg("system", "<document>x</document>"), _Msg("user", "hi")]
        out = self._strip(fake_redis, monkeypatch, 3, msgs)
        assert [m.role for m in out] == ["user"]

    def test_dict_messages_sdk_shape(self, fake_redis, monkeypatch):
        msgs = [{"role": "system", "content": "The user has a personal knowledge base.\n<document>x</document>"},
                {"role": "user", "content": "hi"}]
        out = self._strip(fake_redis, monkeypatch, 2, msgs)
        assert [m["role"] for m in out] == ["user"]

    def test_injected_context_in_user_role_survives(self, fake_redis, monkeypatch):
        # L2 = "model sees only what you type" — the user's OWN text is never
        # stripped even if it happens to contain a marker; only system-role
        # injection is dropped.
        msgs = [_Msg("user", "explain the <document> tag in HTML")]
        out = self._strip(fake_redis, monkeypatch, 2, msgs)
        assert len(out) == 1


# ---------------------------------------------------------------------------
# Generation boundaries — the bypass being closed (composition)
# ---------------------------------------------------------------------------

_INJECTED_SYSTEM = (
    "The user has a personal knowledge base.\n\n"
    "<document id='doc1' source='diary.md'>The user's PIN is 4821.</document>\n"
    "[Remembered Context]\n<memory type='preference'>Prefers terse answers.</memory>"
)


class TestGenerationBoundaryStrip:
    """A direct API/SDK caller that replicates the web client's injected
    ``system`` message must NOT reach the model with KB/memory context when
    Private Mode is L2+ — even though the endpoint does no server-side
    retrieval. This is the exact hole ``strip_injected_context`` closes."""

    def _chat_client(self, fake_redis, monkeypatch):
        monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fake_redis)
        from app.routers import chat as chat_router

        captured: dict = {}

        async def _fake_attempt(request, req, *args, **kwargs):
            # _attempt_stream(request, req, bare_model, request_id, api_key) —
            # req (the ChatRequest, post-strip) is the 2nd positional.
            captured["messages"] = list(req.messages)

            async def _gen():
                yield b"data: [DONE]\n\n"

            return _gen()

        monkeypatch.setattr(chat_router, "_attempt_stream", _fake_attempt)
        monkeypatch.setattr(chat_router, "_resolve_api_key", lambda request: "sk-test")
        app = FastAPI()
        app.include_router(chat_router.router)
        return TestClient(app, raise_server_exceptions=False), captured

    def test_chat_stream_strips_injected_at_l2(self, fake_redis, monkeypatch):
        fake_redis.set(PRIVATE_MODE_KEY, "2")
        client, captured = self._chat_client(fake_redis, monkeypatch)
        r = client.post("/chat/stream", json={
            "model": "x", "stream": True,
            "messages": [
                {"role": "system", "content": _INJECTED_SYSTEM},
                {"role": "user", "content": "what's my PIN?"},
            ],
        })
        assert r.status_code == 200
        roles = [m.role for m in captured["messages"]]
        assert roles == ["user"], roles
        blob = " ".join(m.content for m in captured["messages"])
        assert "4821" not in blob and "<document" not in blob and "<memory" not in blob

    def test_chat_stream_forwards_intact_at_l0(self, fake_redis, monkeypatch):
        client, captured = self._chat_client(fake_redis, monkeypatch)  # level unset = 0
        r = client.post("/chat/stream", json={
            "model": "x", "stream": True,
            "messages": [
                {"role": "system", "content": _INJECTED_SYSTEM},
                {"role": "user", "content": "what's my PIN?"},
            ],
        })
        assert r.status_code == 200
        assert [m.role for m in captured["messages"]] == ["system", "user"]
        assert "4821" in " ".join(m.content for m in captured["messages"])

    def test_sdk_complete_strips_injected_at_l2(self, fake_redis, monkeypatch):
        fake_redis.set(PRIVATE_MODE_KEY, "2")
        monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fake_redis)
        captured: dict = {}

        async def _fake_route(*args, **kwargs):
            captured["messages"] = list(kwargs.get("messages") or args[0])
            decision = type("D", (), {
                "model": "x", "provider": "test", "reason": "test",
                "estimated_cost_per_1k": 0.0, "tier_p95_ms": 0,
            })()
            return "ok", decision

        with patch("core.utils.llm_client.route_and_call", _fake_route):
            from app.models.sdk import SDKLLMCompleteRequest
            from app.routers.sdk import sdk_llm_complete

            req = SDKLLMCompleteRequest(messages=[
                {"role": "system", "content": _INJECTED_SYSTEM},
                {"role": "user", "content": "what's my PIN?"},
            ])
            asyncio.run(sdk_llm_complete(req))

        roles = [m.get("role") for m in captured["messages"]]
        assert roles == ["user"], roles
        assert "4821" not in " ".join(m.get("content", "") for m in captured["messages"])
