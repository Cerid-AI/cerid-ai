# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-1j verifiability harness — CONSUMER-ISOLATION CLOSE-OUT probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 1.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-087).

Consumer domain isolation (X-Client-ID -> CONSUMER_REGISTRY -> allowed_domains /
strict_domains) was applied only inside the canonical ``/agent/query`` handler.
The legacy ``POST /query`` and ``POST /custom-agents/{id}/query`` handlers called
the retrieval pipeline WITHOUT reading X-Client-ID, so a consumer restricted to
its allow-list (e.g. cerid-finance -> [finance, general], trading-agent ->
[trading]) escaped the wall simply by using those endpoints — a cross-domain read
escalation the SDK contract claims is impossible.

This probe drives the REAL legacy ``/query`` (spying the core retrieval) and the
REAL custom-agent handler (spying the guarded seam) with an X-Client-ID header and
asserts the consumer's allowed_domains / strict_domains are resolved and applied.
RED-then-GREEN; GREEN -> preservation gates.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest

# Drives the agent-query path, which calls get_redis() internally — the
# dependency is invisible to fixture-shape inference, so declare it.
pytestmark = pytest.mark.live_stack

_FINANCE = ["finance", "general"]


class _FakeRequest:
    def __init__(self, headers: dict | None = None):
        self.headers = headers or {}


class _KwargSpy:
    def __init__(self, ret):
        self._ret = ret
        self.kwargs: dict | None = None

    async def __call__(self, *args, **kwargs):
        self.kwargs = kwargs
        return dict(self._ret)


_RESULT = {"context": "", "sources": [], "results": [], "confidence": 0.0,
           "domains_searched": [], "total_results": 0}


def _neutral_private(monkeypatch):
    fr = fakeredis.FakeRedis(decode_responses=True)  # L0
    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fr)


# ---------------------------------------------------------------------------
# CR-087 — legacy /query must resolve + apply consumer isolation.
# ---------------------------------------------------------------------------

@pytest.mark.preservation
async def test_legacy_query_resolves_consumer_isolation(monkeypatch):
    """A restricted consumer (trading-agent -> [trading]) hitting POST /query must
    be walled to its allowed_domains, not read across all domains. RED on HEAD:
    /query reads no X-Client-ID (CR-087)."""
    _neutral_private(monkeypatch)
    for g in ("get_chroma", "get_redis", "get_neo4j", "get_graph_store"):
        monkeypatch.setattr(f"app.routers.query.{g}", lambda: MagicMock(), raising=False)
    spy = _KwargSpy(_RESULT)
    monkeypatch.setattr("core.agents.query_agent.agent_query_full", spy)

    from app.routers.query import QueryRequest, query_endpoint
    await query_endpoint(
        QueryRequest(query="who owns the trading book?", domain="finance"),
        _FakeRequest({"x-client-id": "trading-agent"}),
    )

    assert spy.kwargs is not None, "legacy /query never reached retrieval"
    assert spy.kwargs.get("allowed_domains") == ["trading"], (
        "legacy /query did not resolve the consumer's allowed_domains — a "
        "restricted consumer reads across all domains via /query (CR-087)"
    )
    assert spy.kwargs.get("strict_domains") is True, (
        "legacy /query did not resolve the consumer's strict_domains (CR-087)"
    )


async def test_legacy_query_gui_unrestricted_green_anchor(monkeypatch):
    """Green anchor: an unrestricted caller (no / gui X-Client-ID) still reads
    all domains — the fix must not over-restrict the default consumer."""
    _neutral_private(monkeypatch)
    for g in ("get_chroma", "get_redis", "get_neo4j", "get_graph_store"):
        monkeypatch.setattr(f"app.routers.query.{g}", lambda: MagicMock(), raising=False)
    spy = _KwargSpy(_RESULT)
    monkeypatch.setattr("core.agents.query_agent.agent_query_full", spy)

    from app.routers.query import QueryRequest, query_endpoint
    await query_endpoint(QueryRequest(query="q", domain="finance"), _FakeRequest({}))

    assert spy.kwargs is not None
    assert spy.kwargs.get("allowed_domains") is None, "gui default must stay unrestricted"


# ---------------------------------------------------------------------------
# CR-087 — custom-agent query must resolve + apply consumer isolation.
# ---------------------------------------------------------------------------

@pytest.mark.preservation
async def test_custom_agent_resolves_consumer_isolation(monkeypatch):
    """A restricted consumer hitting POST /custom-agents/{id}/query must be walled
    to its allowed_domains. RED on HEAD: the handler reads no X-Client-ID and
    resolves the default (gui, unrestricted) (CR-087)."""
    _neutral_private(monkeypatch)
    for g in ("get_chroma", "get_redis", "get_neo4j", "get_graph_store"):
        monkeypatch.setattr(f"app.routers.custom_agents.{g}", lambda: MagicMock(), raising=False)
    monkeypatch.setattr(
        "app.db.neo4j.agents.get_agent",
        lambda driver, agent_id: {"id": agent_id, "domains": None,
                                  "rag_mode": "manual", "model_override": None},
    )
    seam = _KwargSpy(_RESULT)
    monkeypatch.setattr("core.agents.guarded_retrieval.guarded_agent_query_full", seam)

    from app.routers.custom_agents import AgentQueryRequest, query_agent
    await query_agent(
        "a1", AgentQueryRequest(query="q"),
        _FakeRequest({"x-client-id": "cerid-finance"}),
    )

    assert seam.kwargs is not None, "custom-agent query never reached the guarded seam"
    ctx = seam.kwargs["request_context"]
    assert ctx.allowed_domains_list() == _FINANCE, (
        "custom-agent query did not resolve the consumer's allowed_domains — a "
        "restricted consumer reads across all domains via a custom agent (CR-087)"
    )
    assert ctx.strict_domains is True
