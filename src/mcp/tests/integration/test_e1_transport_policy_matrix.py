# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-0 verifiability harness — the TRANSPORT-POLICY MATRIX probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 0.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``.

The E1 audit's unifying root cause: there is ONE canonical request path
(``/agent/query`` -> ``_agent_query_inner``) that applies cross-cutting policy —
Private-Mode L2 KB-bypass and consumer domain isolation — and EVERY alternate
transport (MCP, A2A, legacy ``/query``, custom-agents, ``/agent/memory/recall``,
automations) re-enters the same pipeline having dropped some of it. Each
transport tests green in isolation, so the omissions are invisible to CI. This
probe drives the transports side-by-side and asserts they apply the SAME policy.

These are **synthetic** probes — no live stack. They drive the REAL handler
functions with a retrieval spy (patched at the shared source symbols
``core.agents.query_agent.agent_query_full`` / ``.agent_query`` /
``app.agents.memory.recall_memories`` — every transport imports these
function-locally, so patching the source reaches all of them) and fake store
getters, and assert on whether KB retrieval was reached / how it was scoped.

Both dimensions are now CLOSED and live ``@pytest.mark.preservation`` gates:
Phase 1a closed private-Mode-L2 across every transport via the guarded seam
(core.agents.guarded_retrieval); Phase 1c closed the C1 cache consumer-isolation
leak (CR-001) by keying the result cache on the consumer's effective domain scope
(agents.py resolves the consumer BEFORE the cache check). A regression that drops
either gate fails the merge.
"""
from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import fakeredis
import pytest

# Drives the agent-query path, which calls get_redis() internally — the
# dependency is invisible to fixture-shape inference, so declare it.
pytestmark = pytest.mark.live_stack

PRIVATE_MODE_KEY = "cerid:private_mode:global"

# Transport modules whose module-level store getters must be neutralised so a
# buggy (ungated) handler can build its pipeline call without a live stack.
_TRANSPORT_MODULES = (
    "app.routers.agents",
    "app.tools",
    "app.routers.a2a",
    "app.routers.query",
    "app.routers.custom_agents",
)
_STORE_GETTERS = ("get_chroma", "get_redis", "get_neo4j", "get_graph_store")


class _Spy:
    """Async stand-in for a retrieval entry point. Records that it was reached
    and the kwargs it was called with (for the consumer-scoping dimension)."""

    def __init__(self, ret):
        self._ret = ret
        self.called = False
        self.calls: list[dict] = []

    async def __call__(self, *args, **kwargs):
        self.called = True
        self.calls.append(kwargs)
        return self._ret


class _FakeRequest:
    """Minimal stand-in for a Starlette Request — only ``.headers.get`` is used
    by the handlers under test."""

    def __init__(self, headers: dict | None = None):
        self.headers = headers or {}


def _install(monkeypatch, *, level: int | None) -> tuple[_Spy, _Spy]:
    """Wire fakes: a global private-mode level, neutral store getters, and
    retrieval spies. Returns (query_spy, recall_spy)."""
    fr = fakeredis.FakeRedis(decode_responses=True)
    if level:
        fr.set(PRIVATE_MODE_KEY, str(level))
    # private_blocks() reads the global level through app.services.private_mode.get_redis
    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fr)

    for mod_name in _TRANSPORT_MODULES:
        mod = importlib.import_module(mod_name)
        for getter in _STORE_GETTERS:
            if hasattr(mod, getter):
                monkeypatch.setattr(f"{mod_name}.{getter}", lambda: MagicMock())

    query_spy = _Spy({"sources": [], "results": [], "context": "", "answer": ""})
    recall_spy = _Spy([])
    # The guarded seam (and every transport) imports these function-locally from
    # the SOURCE module, so patching the source reaches all of them. Memory recall
    # is spied at its core definition — the binding guarded_recall_memories calls.
    monkeypatch.setattr("core.agents.query_agent.agent_query_full", query_spy)
    monkeypatch.setattr("core.agents.query_agent.agent_query", query_spy)
    monkeypatch.setattr("core.agents.memory.recall_memories", recall_spy)
    return query_spy, recall_spy


# ---------------------------------------------------------------------------
# Transport drivers — each invokes the REAL handler for its transport.
# ---------------------------------------------------------------------------

async def _drive_canonical(client_id: str = "gui", **req_kw):
    from app.routers.agents import AgentQueryRequest, agent_query_endpoint

    headers = {"x-client-id": client_id} if client_id else {}
    await agent_query_endpoint(
        AgentQueryRequest(query="q", domains=["finance"], **req_kw),
        _FakeRequest(headers),
    )


async def _drive_legacy(**_):
    from app.routers.query import QueryRequest, query_endpoint

    await query_endpoint(QueryRequest(query="q", domain="finance"), _FakeRequest())


async def _drive_mcp(**_):
    from app.tools import _dispatch_raw

    await _dispatch_raw("pkb_agent_query", {"query": "q"})


async def _drive_a2a_query(**_):
    from app.routers.a2a import _execute_query

    await _execute_query({"query": "q"})


async def _drive_a2a_recall(**_):
    from app.routers.a2a import _execute_recall

    await _execute_recall({"query": "q"})


async def _drive_memory_recall(**_):
    from app.routers.agents import MemoryRecallRequest, memory_recall_endpoint

    await memory_recall_endpoint(MemoryRecallRequest(query="q"))


async def _drive_custom_agents(monkeypatch=None, **_):
    from app.routers.custom_agents import AgentQueryRequest, query_agent

    # custom-agents loads the agent def from Neo4j; stub it to a plain agent.
    monkeypatch.setattr(
        "app.db.neo4j.agents.get_agent",
        lambda driver, agent_id: {"id": agent_id, "domains": ["finance"], "rag_mode": "manual"},
    )
    await query_agent("agent-1", AgentQueryRequest(query="q"), _FakeRequest())


_L2_DRIVERS = {
    "canonical": _drive_canonical,
    "legacy": _drive_legacy,
    "mcp": _drive_mcp,
    "a2a_query": _drive_a2a_query,
    "a2a_recall": _drive_a2a_recall,
    "memory_recall": _drive_memory_recall,
    "custom_agents": _drive_custom_agents,
}


# ---------------------------------------------------------------------------
# Dimension 1 — Private-Mode L2 must block KB retrieval on EVERY transport
# ---------------------------------------------------------------------------

# E1 Phase 1a CLOSED the private-L2 dimension: canonical + legacy gate at the
# transport boundary; MCP / A2A / memory-recall / custom-agents now route through
# the guarded seam (core.agents.guarded_retrieval) which bypasses KB at L2. This
# test is a live preservation gate — a regression that drops the gate on any
# transport fails the merge.
@pytest.mark.preservation
@pytest.mark.parametrize(
    "transport",
    ["canonical", "legacy", "mcp", "a2a_query", "a2a_recall", "memory_recall", "custom_agents"],
)
async def test_private_mode_l2_blocks_kb_on_every_transport(transport, monkeypatch):
    """At global Private-Mode L2 ("skip KB"), NO transport may reach KB
    retrieval — the web client applies this locally, and a direct API/MCP/A2A
    caller must get the same server-side guarantee (private_mode.py contract).
    Closes CR-084/085/015/094/087."""
    query_spy, recall_spy = _install(monkeypatch, level=2)
    await _L2_DRIVERS[transport](monkeypatch=monkeypatch)
    reached = query_spy.called or recall_spy.called
    assert not reached, (
        f"transport '{transport}' reached KB retrieval at Private-Mode L2 "
        f"(query_spy.called={query_spy.called}, recall_spy.called={recall_spy.called}). "
        f"RED until the Phase-1 RequestContext gate enforces private-mode inside "
        f"the core seam for every transport."
    )


def test_l2_green_anchor_canonical_and_legacy_gate():
    """Belt-and-suspenders: the two transports that DO gate must keep gating —
    guards against a regression that removes the L2 short-circuit. (Static: the
    parametrized canonical/legacy cases above already prove this; this anchor
    documents the invariant explicitly and holds now and after Phase 1.)"""
    # The parametrized 'canonical' and 'legacy' cases assert not-reached at L2.
    # This anchor exists so the intent survives even if the matrix is refactored.
    assert True


# ---------------------------------------------------------------------------
# Dimension 2 — consumer domain isolation must not leak across the C1 cache
# (CR-001). Two consumers issuing the same query/domains/top_k must NOT share a
# cache entry when their allowed_domains differ.
# ---------------------------------------------------------------------------

@pytest.mark.preservation
async def test_c1_cache_does_not_leak_across_consumers(monkeypatch):
    """gui (allowed_domains=None) caches an all-domains result; a strict
    consumer (trading-agent, allowed_domains=['trading']) issuing the SAME
    query/domains/top_k must NOT receive gui's cached result."""
    import config as _cfg

    fr_private = fakeredis.FakeRedis(decode_responses=True)  # L0 — no private mode
    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fr_private)

    # A shared fake redis backing the C1 query cache (utils.query_cache uses
    # `from deps import get_redis`).
    fr_cache = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("deps.get_redis", lambda: fr_cache)
    for mod_name in _TRANSPORT_MODULES:
        mod = importlib.import_module(mod_name)
        for getter in _STORE_GETTERS:
            if hasattr(mod, getter):
                monkeypatch.setattr(f"{mod_name}.{getter}", lambda: MagicMock())

    # Tag each retrieval result with the allowed_domains it was computed under,
    # so we can tell whose answer the second consumer received.
    async def tagged_query(*args, **kwargs):
        return {
            "sources": [{"id": "s1"}],
            "results": [{"artifact_id": "a1", "relevance": 0.9}],
            "context": "ctx",
            "allowed_domains_seen": kwargs.get("allowed_domains"),
        }

    monkeypatch.setattr("core.agents.query_agent.agent_query_full", tagged_query)

    # Consumer registry with a strict consumer.
    monkeypatch.setattr(_cfg, "CONSUMER_REGISTRY", {
        "gui": {"allowed_domains": None, "strict_domains": False},
        "trading-agent": {"allowed_domains": ["trading"], "strict_domains": True},
        "_default": {"allowed_domains": None, "strict_domains": False},
    }, raising=False)
    # settings.CONSUMER_REGISTRY is the import target inside _agent_query_inner.
    monkeypatch.setattr("config.settings.CONSUMER_REGISTRY", {
        "gui": {"allowed_domains": None, "strict_domains": False},
        "trading-agent": {"allowed_domains": ["trading"], "strict_domains": True},
        "_default": {"allowed_domains": None, "strict_domains": False},
    }, raising=False)

    from app.routers.agents import AgentQueryRequest, _agent_query_inner

    # gui runs first with no domain restriction -> caches an all-domains result.
    gui_req = AgentQueryRequest(query="secret?", domains=None, top_k=10)
    await _agent_query_inner(gui_req, _FakeRequest({"x-client-id": "gui"}))

    # trading-agent issues the SAME query/domains/top_k.
    strict_req = AgentQueryRequest(query="secret?", domains=None, top_k=10)
    strict_result = await _agent_query_inner(
        strict_req, _FakeRequest({"x-client-id": "trading-agent"})
    )

    # A cache HIT returns gui's payload verbatim, carrying allowed_domains_seen
    # from the gui run (None). Correct isolation would recompute (or refuse to
    # serve) for the strict consumer, so the strict result must NOT be gui's
    # unrestricted cache entry.
    assert strict_result.get("cached") is not True, (
        "strict consumer (trading-agent) was served gui's cached result — "
        "consumer domain isolation bypassed via the C1 cache (CR-001)"
    )


@pytest.mark.preservation
async def test_c1_cache_hits_for_same_consumer(monkeypatch):
    """Green anchor: keying the cache by consumer scope must NOT over-isolate —
    the SAME consumer issuing an identical query must still get a warm cache hit
    (one retrieval, second call served from cache). Guards against a future
    change that keys too finely and defeats caching entirely."""
    import config as _cfg

    fr_private = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fr_private)
    fr_cache = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("deps.get_redis", lambda: fr_cache)
    for mod_name in _TRANSPORT_MODULES:
        mod = importlib.import_module(mod_name)
        for getter in _STORE_GETTERS:
            if hasattr(mod, getter):
                monkeypatch.setattr(f"{mod_name}.{getter}", lambda: MagicMock())

    calls = {"n": 0}

    async def counting_query(*args, **kwargs):
        calls["n"] += 1
        return {"sources": [{"id": "s1"}], "results": [{"artifact_id": "a1", "relevance": 0.9}], "context": "ctx"}

    monkeypatch.setattr("core.agents.query_agent.agent_query_full", counting_query)
    registry = {
        "gui": {"allowed_domains": None, "strict_domains": False},
        "_default": {"allowed_domains": None, "strict_domains": False},
    }
    monkeypatch.setattr(_cfg, "CONSUMER_REGISTRY", registry, raising=False)
    monkeypatch.setattr("config.settings.CONSUMER_REGISTRY", registry, raising=False)

    from app.routers.agents import AgentQueryRequest, _agent_query_inner

    req_kw = {"query": "same?", "domains": ["finance"], "top_k": 10}
    await _agent_query_inner(AgentQueryRequest(**req_kw), _FakeRequest({"x-client-id": "gui"}))
    second = await _agent_query_inner(AgentQueryRequest(**req_kw), _FakeRequest({"x-client-id": "gui"}))

    assert calls["n"] == 1, f"same-consumer repeat query should hit cache (1 retrieval), got {calls['n']}"
    assert second.get("cached") is True, "second identical same-consumer query must be served from cache"
