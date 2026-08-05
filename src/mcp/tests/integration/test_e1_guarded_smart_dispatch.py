# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-1i verifiability harness — GUARDED SMART-DISPATCH probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 1.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-095).

The custom-agent query handler always calls ``guarded_agent_query_full`` (the
MANUAL kb-only path) and only echoes ``agent.rag_mode`` back on the response — so
an agent configured ``rag_mode='smart'`` (the stored default) never gets the
smart-mode kb/memory/external orchestration. The reason it was stuck on manual:
the smart path (``orchestrated_query``) was not behind the Phase-1a guarded seam,
so routing to it would have dropped Private-Mode + consumer isolation.

The fix adds ``guarded_orchestrated_query`` — the smart-mode sibling of
``core.guarded_agent_query_full``: Private-Mode L2 short-circuits to the KB-bypass
envelope, consumer isolation + honored directives come from the RequestContext,
and only then does it call ``orchestrated_query``. The custom-agent handler routes
by ``rag_mode`` through it.

This probe drives the REAL custom-agent handler (routing) and the REAL guarded
seam (guard preservation). RED-then-GREEN; GREEN → preservation gates.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest

_STORES = ("get_chroma", "get_redis", "get_neo4j", "get_graph_store")
_RESULT = {"context": "", "sources": [], "results": [], "confidence": 0.0,
           "domains_searched": [], "total_results": 0}


class _FakeRequest:
    def __init__(self, headers: dict | None = None):
        self.headers = headers or {}


class _AsyncSpy:
    def __init__(self, ret):
        self._ret = ret
        self.calls = 0
        self.kwargs: dict | None = None

    async def __call__(self, *args, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        return dict(self._ret)


def _wire(monkeypatch, rag_mode):
    fr = fakeredis.FakeRedis(decode_responses=True)  # L0 — no private mode
    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fr)
    for g in _STORES:
        monkeypatch.setattr(f"app.routers.custom_agents.{g}", lambda: MagicMock(), raising=False)
    monkeypatch.setattr(
        "app.db.neo4j.agents.get_agent",
        lambda driver, agent_id: {
            "id": agent_id, "domains": ["finance"],
            "rag_mode": rag_mode, "model_override": None,
        },
    )


# ---------------------------------------------------------------------------
# Routing — a smart agent must run smart orchestration, a manual agent manual.
# ---------------------------------------------------------------------------

@pytest.mark.preservation
async def test_custom_agent_smart_routes_through_orchestrator(monkeypatch):
    """rag_mode='smart' must route through orchestrated_query, NOT the manual
    agent_query_full path. RED on HEAD: the handler always runs manual (CR-095)."""
    _wire(monkeypatch, "smart")
    manual = _AsyncSpy(_RESULT)
    monkeypatch.setattr("core.agents.guarded_retrieval.guarded_agent_query_full", manual)
    orchestrated = _AsyncSpy(_RESULT)
    monkeypatch.setattr("app.agents.retrieval_orchestrator.orchestrated_query", orchestrated)

    from app.routers.custom_agents import AgentQueryRequest, query_agent
    await query_agent("a1", AgentQueryRequest(query="what does the book say?"), _FakeRequest())

    assert manual.calls == 0, (
        "custom-agent smart mode still ran the manual kb-only path — rag_mode is "
        "echo-only (CR-095)"
    )
    assert orchestrated.calls == 1, (
        "custom-agent smart mode did not route through orchestrated_query (CR-095)"
    )


async def test_custom_agent_manual_routes_through_agent_query_full(monkeypatch):
    """Green anchor: a manual agent keeps the manual guarded path."""
    _wire(monkeypatch, "manual")
    manual = _AsyncSpy(_RESULT)
    monkeypatch.setattr("core.agents.guarded_retrieval.guarded_agent_query_full", manual)
    orchestrated = _AsyncSpy(_RESULT)
    monkeypatch.setattr("app.agents.retrieval_orchestrator.orchestrated_query", orchestrated)

    from app.routers.custom_agents import AgentQueryRequest, query_agent
    await query_agent("a1", AgentQueryRequest(query="q"), _FakeRequest())

    assert manual.calls == 1
    assert orchestrated.calls == 0


# ---------------------------------------------------------------------------
# Guard preservation — the smart seam must enforce Private-Mode L2 + isolation.
# ---------------------------------------------------------------------------

@pytest.mark.preservation
async def test_guarded_orchestrated_query_blocks_kb_at_l2(monkeypatch):
    """guarded_orchestrated_query must short-circuit to the KB-bypass envelope at
    Private-Mode L2 — never reaching orchestrated_query — so smart-mode routing
    does not reopen the private-mode bypass Phase 1a closed. RED on HEAD: the
    seam does not exist (CR-095)."""
    from app.agents.retrieval_orchestrator import guarded_orchestrated_query
    from core.agents.request_context import RequestContext

    orchestrated = _AsyncSpy(_RESULT)
    monkeypatch.setattr("app.agents.retrieval_orchestrator.orchestrated_query", orchestrated)

    result = await guarded_orchestrated_query(
        request_context=RequestContext(private_level=2),
        query="what are my private notes?",
        chroma_client=MagicMock(),
        redis_client=MagicMock(),
        neo4j_driver=MagicMock(),
    )

    assert orchestrated.calls == 0, (
        "guarded smart dispatch reached KB retrieval at Private-Mode L2 — the "
        "private-mode bypass Phase 1a closed would reopen on the smart path"
    )
    # E1 CR-032/062: empty envelope is the L2 signal (no kb_bypassed stamp).
    assert result.get("results") == []
    assert result.get("total_results") == 0
    assert "kb_bypassed" not in result


@pytest.mark.preservation
async def test_guarded_orchestrated_query_forwards_consumer_isolation(monkeypatch):
    """At L0 the seam must run orchestrated_query AND thread the consumer's
    allowed_domains / strict_domains + honored directives from the context."""
    from app.agents.retrieval_orchestrator import guarded_orchestrated_query
    from core.agents.request_context import RequestContext

    orchestrated = _AsyncSpy(_RESULT)
    monkeypatch.setattr("app.agents.retrieval_orchestrator.orchestrated_query", orchestrated)

    ctx = RequestContext(
        client_id="trading-agent",
        allowed_domains=("trading",),
        strict_domains=True,
        skip_cache=True,
    )
    await guarded_orchestrated_query(
        request_context=ctx,
        query="q",
        chroma_client=MagicMock(),
        redis_client=MagicMock(),
        neo4j_driver=MagicMock(),
    )

    assert orchestrated.calls == 1
    k = orchestrated.kwargs
    assert k.get("allowed_domains") == ["trading"], "smart seam dropped consumer allowed_domains"
    assert k.get("strict_domains") is True, "smart seam dropped strict_domains"
    assert k.get("skip_cache") is True, "smart seam dropped skip_cache directive"
