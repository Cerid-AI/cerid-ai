# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-5 verifiability harness — KB_POOL starvation guard PARITY.

Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-096). Adversarial re-validation confirmed A2A was already closed by CR-091
but four *other* heavy-retrieval transports still reach the ``agent_query_full``
pipeline WITHOUT acquiring ``KB_POOL`` — so concurrent load on any of them
reproduces the exact pool-bypass starvation of ``/health`` + ``/observability``
the guard was shipped to prevent:

  * ``/query``                     (query.py :query_endpoint)
  * MCP ``pkb_agent_query``        (tools.py :_dispatch_raw)
  * ``/custom-agents/{id}/query``  (custom_agents.py :query_agent)
  * scheduled automations          (automations.py :execute_automation)

The fix wraps each in ``async with KB_POOL.acquire():`` — the same shape shipped
for A2A in CR-091 (a2a.py). NOT moved into ``agent_query_full`` itself:
import-linter forbids ``core`` importing ``app.concurrency``, and the function is
shared by eval/HyDE/ablation paths that intentionally run unbounded.

Synthetic probes — no live stack. RED-then-GREEN; GREEN → preservation gates.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest


class _AsyncSpy:
    def __init__(self, ret):
        self._ret = ret
        self.calls = 0

    async def __call__(self, *args, **kwargs):
        self.calls += 1
        return dict(self._ret)


class _FakePool:
    """Records KB_POOL.acquire() usage; acquire() is an async context manager."""

    def __init__(self):
        self.acquired = 0

    def acquire(self):
        outer = self

        class _CM:
            async def __aenter__(self):
                outer.acquired += 1
                return None

            async def __aexit__(self, *exc):
                return False

        return _CM()


def _fake_request():
    req = MagicMock()
    req.headers = {"x-client-id": "gui"}
    return req


def _priv(monkeypatch):
    fr = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fr)


def _stores(monkeypatch, module):
    for g in ("get_chroma", "get_redis", "get_neo4j", "get_graph_store"):
        monkeypatch.setattr(f"{module}.{g}", lambda: MagicMock(), raising=False)


def _pool(monkeypatch) -> _FakePool:
    pool = _FakePool()
    monkeypatch.setattr("app.concurrency.KB_POOL", pool)
    return pool


@pytest.mark.preservation
async def test_query_endpoint_acquires_kb_pool(monkeypatch):
    """POST /query must run retrieval under KB_POOL. RED on HEAD: query_endpoint
    calls agent_query_full ungated (CR-096)."""
    _priv(monkeypatch)
    _stores(monkeypatch, "app.routers.query")
    pool = _pool(monkeypatch)
    seam = _AsyncSpy({"context": "", "sources": [], "confidence": 0.0, "results": []})
    monkeypatch.setattr("core.agents.query_agent.agent_query_full", seam)

    from app.routers.query import QueryRequest, query_endpoint

    await query_endpoint(QueryRequest(query="who owns the trading book?"), _fake_request())

    assert seam.calls == 1, "/query never reached the retrieval seam"
    assert pool.acquired == 1, "/query did not acquire KB_POOL (CR-096)"


@pytest.mark.preservation
async def test_pkb_agent_query_acquires_kb_pool(monkeypatch):
    """MCP pkb_agent_query must run retrieval under KB_POOL. RED on HEAD: the
    tool dispatcher calls guarded_agent_query_full ungated (CR-096)."""
    _priv(monkeypatch)
    _stores(monkeypatch, "app.tools")
    pool = _pool(monkeypatch)
    seam = _AsyncSpy({"context": "", "sources": [], "results": []})
    monkeypatch.setattr("core.agents.guarded_retrieval.guarded_agent_query_full", seam)

    from app.tools import _dispatch_raw

    await _dispatch_raw("pkb_agent_query", {"query": "who owns the trading book?"})

    assert seam.calls == 1, "pkb_agent_query never reached the guarded seam"
    assert pool.acquired == 1, "pkb_agent_query did not acquire KB_POOL (CR-096)"


@pytest.mark.preservation
async def test_custom_agent_query_acquires_kb_pool(monkeypatch):
    """POST /custom-agents/{id}/query must run retrieval under KB_POOL. RED on
    HEAD: query_agent calls the guarded seam ungated (CR-096)."""
    _priv(monkeypatch)
    _stores(monkeypatch, "app.routers.custom_agents")
    pool = _pool(monkeypatch)
    monkeypatch.setattr(
        "app.db.neo4j.agents.get_agent",
        lambda driver, aid: {
            "rag_mode": "smart", "domains": None, "model_override": None,
            "system_prompt": "", "temperature": 0.7, "tools": [],
        },
    )
    seam = _AsyncSpy({"context": "", "sources": [], "results": []})
    monkeypatch.setattr(
        "app.agents.retrieval_orchestrator.guarded_orchestrated_query", seam
    )

    from app.routers.custom_agents import query_agent
    from models.agents import AgentQueryRequest

    await query_agent(
        "agent-1", AgentQueryRequest(query="who owns the book?", stream=False), _fake_request()
    )

    assert seam.calls == 1, "custom-agent query never reached the guarded seam"
    assert pool.acquired == 1, "custom-agent query did not acquire KB_POOL (CR-096)"


@pytest.mark.preservation
async def test_automation_acquires_kb_pool(monkeypatch):
    """Scheduled automations must run retrieval under KB_POOL. RED on HEAD:
    execute_automation calls the guarded seam ungated (CR-096). The seam raises
    after the pool is entered (acquire is on __aenter__, before the seam call),
    so this asserts the wrap without mocking the whole action-dispatch tail —
    execute_automation's own except swallows the sentinel."""
    _priv(monkeypatch)
    _stores(monkeypatch, "app.routers.automations")
    pool = _pool(monkeypatch)
    monkeypatch.setattr("app.routers.automations._save_run", lambda run: None)

    async def _boom(*a, **k):
        raise RuntimeError("stop after acquire")

    monkeypatch.setattr("core.agents.guarded_retrieval.guarded_agent_query_full", _boom)

    from app.routers.automations import Automation, execute_automation

    autom = Automation(
        name="probe", prompt="p", schedule="0 0 * * *",
        id="a1", created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )
    await execute_automation(autom)

    assert pool.acquired == 1, "automation retrieval did not acquire KB_POOL (CR-096)"
