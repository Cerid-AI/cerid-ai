# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-1f (part 2) verifiability harness — ALT-TRANSPORT COMPLETENESS probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 1.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-025, CR-057, plus the C2 ``metadata_filter`` cache-skip noted under CR-001).

Three retrieval-completeness / scope leaks on the alternate answer paths:

- **CR-025**: A2A ``_execute_query`` and MCP ``pkb_agent_query`` call
  ``guarded_agent_query_full`` WITHOUT ``graph_store`` — so graph expansion and
  quality/summary enrichment silently skip (the guard forwards graph_store; the
  callers just never pass it). custom_agents already passes it.
- **CR-057**: the cross-domain affinity "bleed" re-queries ``multi_domain_query``
  without forwarding ``metadata_filter``, so a file-scoped retrieval admits
  out-of-file chunks from adjacent domains.
- **C2 metadata_filter skip** (CR-001 tail): a ``metadata_filter``/``exclude_packs``
  narrowing request must not read from (or populate) the general semantic cache —
  a document-scoped MCP query would otherwise hit an unscoped C2 entry.

Synthetic probes — no live stack. CR-025 drives the REAL A2A/MCP handlers with a
spy on the guarded seam; CR-057 + the C2 skip drive the REAL ``_agent_query_impl``
with retrieval stubbed. RED-then-GREEN; GREEN → ``@pytest.mark.preservation``.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest

import core.agents.query_agent as qa

_MDF = {"filename": "report.pdf"}


class _Spy:
    def __init__(self, ret):
        self._ret = ret
        self.kwargs: dict | None = None

    async def __call__(self, *args, **kwargs):
        self.kwargs = kwargs
        return self._ret


class _SyncSpy:
    """For sync seams (cache_lookup is a plain function, not a coroutine)."""

    def __init__(self, ret):
        self._ret = ret
        self.kwargs: dict | None = None

    def __call__(self, *args, **kwargs):
        self.kwargs = kwargs
        return self._ret


def _neutral_stores(monkeypatch, module):
    for getter in ("get_chroma", "get_redis", "get_neo4j", "get_graph_store"):
        monkeypatch.setattr(f"{module}.{getter}", lambda: MagicMock(), raising=False)


# ---------------------------------------------------------------------------
# CR-025 — A2A + MCP query paths must pass graph_store into the guarded seam.
# ---------------------------------------------------------------------------

@pytest.mark.preservation
@pytest.mark.parametrize("driver", ["a2a", "mcp"])
async def test_alt_transports_pass_graph_store(monkeypatch, driver):
    """The A2A knowledge-query skill and MCP pkb_agent_query must forward a
    non-None graph_store so graph expansion + quality enrichment run — parity
    with /agent/query and custom_agents. RED on HEAD: both omit it (CR-025)."""
    spy = _Spy({"context": "", "sources": [], "results": [], "confidence": 0.0,
                "domains_searched": [], "total_results": 0})
    # Both handlers import the guard function-locally from its source module.
    monkeypatch.setattr("core.agents.guarded_retrieval.guarded_agent_query_full", spy)

    if driver == "a2a":
        _neutral_stores(monkeypatch, "app.routers.a2a")
        from app.routers.a2a import _execute_query
        await _execute_query({"query": "who owns the trading book?"})
    else:
        _neutral_stores(monkeypatch, "app.tools")
        from app.tools import _dispatch_raw
        await _dispatch_raw("pkb_agent_query", {"query": "who owns the trading book?"})

    assert spy.kwargs is not None, f"{driver} never reached the guarded seam"
    assert spy.kwargs.get("graph_store") is not None, (
        f"{driver} transport omitted graph_store — graph expansion + quality "
        f"enrichment silently skipped (CR-025)"
    )


# ---------------------------------------------------------------------------
# Shared harness for driving the real _agent_query_impl with retrieval stubbed.
# ---------------------------------------------------------------------------

def _stub_impl(monkeypatch, *, intent="general", adjacents=None):
    monkeypatch.setattr(qa, "_surface_route_dict", lambda q: {"intent": intent})
    monkeypatch.setattr(qa, "_get_adjacent_domains", lambda domains: adjacents or {})


# ---------------------------------------------------------------------------
# CR-057 — the cross-domain bleed must forward metadata_filter.
# ---------------------------------------------------------------------------

@pytest.mark.preservation
async def test_cross_domain_bleed_forwards_metadata_filter(monkeypatch):
    """With strict_domains=False and a partial domain set, the adjacent-domain
    re-query must carry metadata_filter so a file-scoped answer cannot admit
    out-of-file chunks from adjacent domains. RED on HEAD: the bleed omits it."""
    _stub_impl(monkeypatch, intent="general", adjacents={"finance": 0.5})

    calls: list[dict] = []

    async def _spy_mdq(*a, **k):
        calls.append(k)
        return []
    monkeypatch.setattr(qa, "multi_domain_query", _spy_mdq)

    await qa._agent_query_impl(
        query="what does report.pdf conclude?",
        domains=["notes"],
        strict_domains=False,
        metadata_filter=_MDF,
        use_reranking=False,
        skip_cache=True,
        chroma_client=MagicMock(),
        redis_client=None,
        neo4j_driver=MagicMock(),
    )

    bleed = [c for c in calls if c.get("domains") == ["finance"]]
    assert bleed, f"cross-domain bleed never fired (multi_domain_query calls: {[c.get('domains') for c in calls]})"
    assert bleed[0].get("metadata_filter") == _MDF, (
        "cross-domain affinity bleed dropped metadata_filter — a file-scoped "
        f"query admits out-of-file adjacent-domain chunks (got "
        f"{bleed[0].get('metadata_filter')!r}) (CR-057)"
    )


# ---------------------------------------------------------------------------
# C2 skip — a metadata_filter / exclude_packs request must bypass the semantic
# cache entirely (no read, no populate).
# ---------------------------------------------------------------------------

def _install_cache_spy(monkeypatch):
    """Force the semantic cache on, stub embeddings, and install a (sync)
    cache_lookup spy that early-returns a sentinel when consulted. Returns it."""
    monkeypatch.setattr("config.features.ENABLE_SEMANTIC_CACHE", True, raising=False)
    monkeypatch.setattr("core.utils.embeddings.get_embedding_function",
                        lambda: (lambda texts: [[0.1] * 8 for _ in texts]))
    spy = _SyncSpy({"context": "SENTINEL", "sources": [{"id": "s"}], "results": []})
    monkeypatch.setattr("core.retrieval.semantic_cache.cache_lookup", spy)
    _stub_impl(monkeypatch, intent="general", adjacents={})
    monkeypatch.setattr(qa, "multi_domain_query", _no_results)
    return spy


async def _no_results(*a, **k):
    return []


@pytest.mark.preservation
async def test_semantic_cache_skipped_for_metadata_filter(monkeypatch):
    """A metadata_filter (document-scoped) request must NOT consult the semantic
    cache. RED on HEAD: the lookup guard checks only skip_cache, so cache_lookup
    fires and can serve an unscoped C2 entry (CR-001 tail)."""
    spy = _install_cache_spy(monkeypatch)
    fr = fakeredis.FakeRedis(decode_responses=True)

    await qa._agent_query_impl(
        query="what does report.pdf conclude?",
        domains=["notes"],
        metadata_filter=_MDF,
        skip_cache=False,
        use_reranking=False,
        chroma_client=MagicMock(),
        redis_client=fr,
        neo4j_driver=MagicMock(),
    )
    assert spy.kwargs is None, (
        "a metadata_filter (document-scoped) request consulted the general "
        "semantic cache — it can be served an unscoped C2 entry (CR-001 tail)"
    )


@pytest.mark.preservation
async def test_semantic_cache_used_without_metadata_filter(monkeypatch):
    """Green anchor: with no narrowing directive the semantic cache IS consulted
    (and its hit short-circuits) — proves the skip is targeted, not a blanket
    cache disable."""
    spy = _install_cache_spy(monkeypatch)
    fr = fakeredis.FakeRedis(decode_responses=True)

    result = await qa._agent_query_impl(
        query="what is the capital of france?",
        domains=["notes"],
        metadata_filter=None,
        skip_cache=False,
        use_reranking=False,
        chroma_client=MagicMock(),
        redis_client=fr,
        neo4j_driver=MagicMock(),
    )
    assert spy.kwargs is not None, "semantic cache was not consulted for a plain query"
    assert result.get("context") == "SENTINEL", "cache hit did not short-circuit the query"
