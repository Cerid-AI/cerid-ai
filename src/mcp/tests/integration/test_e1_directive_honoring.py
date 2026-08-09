# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-1f verifiability harness — DIRECTIVE-HONORING probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 1.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-009, CR-016).

Two per-request directives are dropped by the ``/agent/query`` dispatch depending
on the RAG-mode branch it lands on:

- **CR-009**: the smart / custom_smart branch calls ``orchestrated_query`` WITHOUT
  forwarding ``skip_cache``, ``metadata_filter``, or ``budget_seconds`` — the
  manual branch forwards all three. So a document-scoped question in smart mode
  (``query_scope='document'`` sets ``skip_cache=True`` + ``metadata_filter=
  {filename}``) searches the whole KB with the nested semantic cache still live,
  and an eval's ``budget_seconds`` override is ignored. The only existing tests of
  this branch patch ``orchestrated_query`` itself and never assert the params.

- **CR-016**: the manual branch reads ``context_sources.{kb,external}`` but never
  ``.memory`` — so ``memory:false`` (the FE 'always' mode's memory opt-out) is
  dropped and recalled personal memories keep entering answers. A privacy-relevant
  contract violation.

These are **synthetic** probes — no live stack. They drive the REAL
``_agent_query_inner`` dispatch with fake store getters and a spy on the seam
(``orchestrated_query`` / ``agent_query_full``), asserting the directive reaches
the seam — the exact coverage the audit found missing. A focused core test spies
on ``_recall_memory_surface`` to prove the ``memory_enabled`` gate actually
suppresses recall.

RED-then-GREEN: written against today's (pre-fix) dispatch — the forwarding
assertions are RED. The Phase-1f fix forwards the smart-branch directives and
threads ``memory_enabled`` from ``context_sources.memory`` through
``agent_query_full`` into the memory-surface gate. Once GREEN these are live
``@pytest.mark.preservation`` gates.
"""
from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import fakeredis
import pytest

PRIVATE_MODE_KEY = "cerid:private_mode:global"
_STORE_GETTERS = ("get_chroma", "get_redis", "get_neo4j", "get_graph_store")


class _FakeRequest:
    def __init__(self, headers: dict | None = None):
        self.headers = headers or {}


class _Spy:
    """Async seam stand-in recording the kwargs it was called with."""

    def __init__(self, ret):
        self._ret = ret
        self.kwargs: dict | None = None

    async def __call__(self, *args, **kwargs):
        self.kwargs = kwargs
        return self._ret


def _install(monkeypatch):
    """L0 private mode, neutral store getters, and a no-op C1 cache so the
    request always reaches the RAG-mode branch."""
    fr = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fr)
    mod = importlib.import_module("app.routers.agents")
    for getter in _STORE_GETTERS:
        if hasattr(mod, getter):
            monkeypatch.setattr(f"app.routers.agents.{getter}", lambda: MagicMock())
    # C1 exact-match cache: force a miss so dispatch runs (utils.query_cache is
    # imported function-locally inside the handler).
    monkeypatch.setattr("utils.query_cache.get_cached", lambda *a, **k: None)
    monkeypatch.setattr("utils.query_cache.set_cached", lambda *a, **k: None)


_SEAM_RESULT = {"context": "", "sources": [], "results": [], "confidence": 0.0,
                "domains_searched": [], "total_results": 0}


# ---------------------------------------------------------------------------
# CR-009 — the smart branch must forward skip_cache / metadata_filter /
# budget_seconds into orchestrated_query (the manual branch already does).
# ---------------------------------------------------------------------------

@pytest.mark.preservation
async def test_smart_mode_forwards_cache_and_scope_directives(monkeypatch):
    """A document-scoped question in smart mode must carry skip_cache=True,
    metadata_filter={filename}, and the budget override into orchestrated_query.
    RED on HEAD: the smart branch drops all three (CR-009)."""
    _install(monkeypatch)
    spy = _Spy(dict(_SEAM_RESULT))
    monkeypatch.setattr("app.agents.retrieval_orchestrator.orchestrated_query", spy)

    from app.routers.agents import AgentQueryRequest, _agent_query_inner

    req = AgentQueryRequest(
        query="what does the report conclude?",
        rag_mode="smart",
        query_scope="document",
        scope_ref="report.pdf",
        budget_seconds=90.0,
    )
    await _agent_query_inner(req, _FakeRequest({"x-client-id": "gui"}))

    assert spy.kwargs is not None, "smart branch never reached orchestrated_query"
    assert spy.kwargs.get("skip_cache") is True, (
        "smart mode dropped skip_cache — the nested semantic cache stays live for "
        "a skip_cache/document-scoped request (CR-009)"
    )
    assert spy.kwargs.get("metadata_filter") == {"filename": "report.pdf"}, (
        "smart mode dropped metadata_filter — document scope becomes whole-KB "
        f"(got {spy.kwargs.get('metadata_filter')!r}) (CR-009)"
    )
    assert spy.kwargs.get("budget_seconds") == 90.0, (
        "smart mode dropped budget_seconds — eval/batch patience override ignored "
        f"(got {spy.kwargs.get('budget_seconds')!r}) (CR-009)"
    )


async def test_manual_mode_forwards_directives_green_anchor(monkeypatch):
    """Green anchor (passes now and after the fix): the manual branch already
    forwards skip_cache / metadata_filter / budget_seconds — pins that the fix
    does not regress the path CR-009 contrasts against."""
    _install(monkeypatch)
    spy = _Spy(dict(_SEAM_RESULT))
    monkeypatch.setattr("core.agents.query_agent.agent_query_full", spy)

    from app.routers.agents import AgentQueryRequest, _agent_query_inner

    req = AgentQueryRequest(
        query="q", rag_mode="manual", query_scope="document",
        scope_ref="report.pdf", budget_seconds=77.0,
    )
    await _agent_query_inner(req, _FakeRequest({"x-client-id": "gui"}))

    assert spy.kwargs is not None
    assert spy.kwargs.get("skip_cache") is True
    assert spy.kwargs.get("metadata_filter") == {"filename": "report.pdf"}
    assert spy.kwargs.get("budget_seconds") == 77.0


# ---------------------------------------------------------------------------
# CR-016 — the manual branch must thread context_sources.memory into the core so
# a memory opt-out actually suppresses the memory surface.
# ---------------------------------------------------------------------------

@pytest.mark.preservation
@pytest.mark.parametrize("memory_flag,expected", [(False, False), (True, True)])
async def test_manual_mode_threads_memory_enabled(monkeypatch, memory_flag, expected):
    """context_sources.memory must reach agent_query_full as memory_enabled.
    RED on HEAD: the manual branch reads only kb/external, never memory (CR-016)."""
    _install(monkeypatch)
    spy = _Spy(dict(_SEAM_RESULT))
    monkeypatch.setattr("core.agents.query_agent.agent_query_full", spy)

    from app.routers.agents import AgentQueryRequest, _agent_query_inner

    req = AgentQueryRequest(
        query="what did I say about my mortgage?",
        rag_mode="manual",
        context_sources={"kb": True, "memory": memory_flag, "external": True},
    )
    await _agent_query_inner(req, _FakeRequest({"x-client-id": "gui"}))

    assert spy.kwargs is not None, "manual branch never reached agent_query_full"
    assert spy.kwargs.get("memory_enabled") is expected, (
        f"context_sources.memory={memory_flag} did not thread to "
        f"memory_enabled={expected!r} (got {spy.kwargs.get('memory_enabled')!r}) — "
        f"the manual path drops the memory opt-out (CR-016)"
    )


@pytest.mark.preservation
@pytest.mark.parametrize("memory_enabled,expect_recall", [(False, False), (True, True)])
async def test_memory_surface_gated_by_memory_enabled(monkeypatch, memory_enabled, expect_recall):
    """Core gate: the personal-context memory surface must fire only when
    memory_enabled is True. Drives the REAL _agent_query_impl with retrieval
    stubbed, spying on _recall_memory_surface. RED on HEAD: _agent_query_impl has
    no memory_enabled param (TypeError)."""
    import core.agents.query_agent as qa

    # Force a personal_context surface route and a single deterministic KB hit,
    # so control reaches the memory-surface gate without a live stack.
    monkeypatch.setattr(qa, "_surface_route_dict",
                        lambda q: {"intent": "personal_context"})

    async def _fake_multi_domain_query(*a, **k):
        return [{"artifact_id": "a1", "relevance": 0.9, "domain": "notes",
                 "content": "x", "filename": "n.md"}]
    monkeypatch.setattr(qa, "multi_domain_query", _fake_multi_domain_query)

    recall_calls = {"n": 0}

    async def _spy_recall(*a, **k):
        recall_calls["n"] += 1
        return []
    monkeypatch.setattr(qa, "_recall_memory_surface", _spy_recall)

    await qa._agent_query_impl(
        query="what did I say about my mortgage?",
        domains=["notes"],
        chroma_client=MagicMock(),
        redis_client=None,          # disables the semantic cache path cleanly
        neo4j_driver=MagicMock(),
        skip_cache=True,
        memory_enabled=memory_enabled,
    )

    fired = recall_calls["n"] > 0
    assert fired is expect_recall, (
        f"memory surface fired={fired} at memory_enabled={memory_enabled} "
        f"(expected {expect_recall}) — memory opt-out not honored in retrieval (CR-016)"
    )
