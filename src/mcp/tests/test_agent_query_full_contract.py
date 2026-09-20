# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Behavioral parity guard for the canonical retrieval entry ``agent_query_full``.

Phase 1 made ``core.agents.query_agent.agent_query_full`` the single path every
surface (REST /agent/query, MCP pkb_agent_query, A2A, custom agents, /query,
/sdk/v1/search) routes through. The retrieval-import-boundary gate enforces the
*structural* invariant (no new bypass importers); these tests lock the
*behavioral* contract so a refactor can't silently drop a stage:

- ``exclude_packs`` is forwarded to the core retrieval primitive (RPB-2 fix).
- provenance (``source_type`` / ``pack_id``) survives the wrapper.
- E1 CR-032: write-only degradation stamps (low_confidence, source_status,
  reranker_status, domains_no_results, kb_bypassed) are NOT on the envelope.
- the conversation-only stub fires when KB is disabled.
- external augmentation is a no-op when the CRAG registry is unwired (so the
  path is safe in tests / the public mirror / any unconfigured deployment).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_CR032_WRITE_ONLY = (
    "low_confidence",
    "source_status",
    "reranker_status",
    "domains_no_results",
    "kb_bypassed",
)


@pytest.mark.asyncio
async def test_forwards_exclude_packs_and_preserves_provenance():
    from core.agents.query_agent import agent_query_full

    fake = {
        "results": [{"relevance": 0.9}],
        "sources": [{"pack_id": "vet-benefits", "source_type": "pack"}],
        "confidence": 0.9,
    }
    with patch("core.agents.query_agent.agent_query", new=AsyncMock(return_value=fake)) as mock_aq:
        out = await agent_query_full(query="q", exclude_packs=True, external_augmentation=False)

    assert mock_aq.call_args.kwargs["exclude_packs"] is True
    # provenance survives the full path
    assert out["sources"][0]["pack_id"] == "vet-benefits"
    assert out["sources"][0]["source_type"] == "pack"
    for key in _CR032_WRITE_ONLY:
        assert key not in out, f"CR-032: {key} must not be stamped on the envelope"


@pytest.mark.asyncio
async def test_no_low_confidence_stamp_on_weak_kb():
    """CR-032: low_confidence is write-only — not stamped even on weak KB."""
    from core.agents.query_agent import agent_query_full

    fake = {"results": [{"relevance": 0.1}], "sources": [], "confidence": 0.1}
    with patch("core.agents.query_agent.agent_query", new=AsyncMock(return_value=fake)):
        out = await agent_query_full(query="q", external_augmentation=False)

    assert "low_confidence" not in out


@pytest.mark.asyncio
async def test_conversation_only_stub_when_kb_disabled():
    from core.agents.query_agent import agent_query_full

    # No agent_query call should happen when KB is gated off.
    with patch("core.agents.query_agent.agent_query", new=AsyncMock()) as mock_aq:
        out = await agent_query_full(query="q", kb_enabled=False, external_augmentation=False)

    mock_aq.assert_not_called()
    assert out["strategy"] == "conversation_only"
    assert out["results"] == []
    for key in _CR032_WRITE_ONLY:
        assert key not in out, f"CR-032: {key} must not be stamped on the envelope"


@pytest.mark.asyncio
async def test_external_augmentation_noop_when_registry_unwired():
    from core.agents import crag
    from core.agents.query_agent import agent_query_full

    # Simulate an unconfigured deployment (no external sources wired). A None
    # registry short-circuits augment_external_crag before the extractor check.
    crag.set_external_source_registry(None)

    fake = {"results": [{"relevance": 0.1}], "sources": [{"source_type": "kb"}], "confidence": 0.1}
    with patch("core.agents.query_agent.agent_query", new=AsyncMock(return_value=fake)):
        out = await agent_query_full(query="q", external_augmentation=True)

    # Gate would fire (weak KB) but firing is a no-op → result unchanged.
    assert out["sources"] == [{"source_type": "kb"}]
    assert "low_confidence" not in out


@pytest.mark.asyncio
async def test_confidence_clamped_to_unit_interval():
    """Quality boost + small-corpus BM25 blends can push relevance past 1;
    confidence is a 0-1 contract (the SDK response model 500'd on 4.79,
    2026-07-10) — the computation site clamps."""
    from core.agents.query_agent import agent_query_full

    fake = {
        "results": [{"relevance": 4.7872}],
        "sources": [{"relevance": 4.7872}],
        "confidence": 4.7872,
    }
    with patch("core.agents.query_agent.agent_query", new=AsyncMock(return_value=fake)):
        out = await agent_query_full(query="q", external_augmentation=False)

    assert 0.0 <= out["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# The wired CRAG merge (F111)
#
# Every test above that touches external augmentation neutralises it first:
# the registry is set to None, or agent_query_full is replaced wholesale, or
# source_breakdown is hand-seeded before the assert. So the suite has 7801
# green tests and zero of them ever run the merge with a registry attached —
# which is the only configuration a real deployment runs in.
#
# These wire a registry and let the real augment_external_crag execute against
# the shape agent_query actually returns (`results` + `sources`, no
# `source_breakdown` — see _agent_query_impl's result_dict).
# ---------------------------------------------------------------------------


class _FakeExternalRegistry:
    """Minimal stand-in for the app-layer DataSourceRegistry."""

    def __init__(self, rows):
        self._rows = rows
        self.calls = 0

    async def query_all(self, search_terms, domain=None, timeout=None):
        self.calls += 1
        return self._rows


@pytest.fixture
def wired_crag():
    """Attach an external registry + extractor, and detach afterwards.

    The DI seam is module-global; leaving it wired would silently change every
    later test in the session.
    """
    from core.agents import crag

    def _wire(rows):
        registry = _FakeExternalRegistry(rows)
        crag.set_external_source_registry(registry)
        crag.set_search_term_extractor(lambda q: q)
        return registry

    yield _wire
    crag.set_external_source_registry(None)
    crag.set_search_term_extractor(None)


def _legacy_kb_result():
    """The shape ``_agent_query_impl`` returns: no ``source_breakdown`` key.

    Relevance is below the CRAG threshold so the gate fires — that is the
    configuration in which the merge runs.
    """
    kb_rows = [
        {"content": "vesting schedule", "relevance": 0.30, "artifact_id": "a1",
         "filename": "equity.md", "source_type": "kb", "domain": "documents"},
        {"content": "cliff date", "relevance": 0.22, "artifact_id": "a2",
         "filename": "equity.md", "source_type": "kb", "domain": "documents"},
    ]
    return {
        "context": "vesting schedule\ncliff date",
        "sources": kb_rows,
        "results": kb_rows,
        "confidence": 0.26,
        "total_results": 2,
        "domains_searched": ["documents"],
    }


@pytest.mark.xfail(
    strict=True,
    reason=(
        "F104: augment_external_crag rebuilds the envelope with "
        "QueryEnvelope.from_legacy_result, which reads kb/memory/external "
        "ONLY from result['source_breakdown']. agent_query does not emit that "
        "key, so every KB result is dropped and the response contains the "
        "external hits alone. Remove this marker in the commit that fixes "
        "crag.py."
    ),
)
@pytest.mark.asyncio
async def test_wired_crag_merge_keeps_every_kb_result(wired_crag):
    from core.agents.query_agent import agent_query_full

    wired_crag([
        {"content": "IRS 83(b) overview", "title": "83(b)", "url": "https://x/1",
         "source_name": "irs", "relevance": 0.9},
    ])

    with patch(
        "core.agents.query_agent.agent_query",
        new=AsyncMock(return_value=_legacy_kb_result()),
    ):
        out = await agent_query_full(query="my vesting cliff", external_augmentation=True)

    kept = {r.get("artifact_id") for r in out["results"] if r.get("source_type") == "kb"}
    assert kept == {"a1", "a2"}, (
        "the KB hits the query already found were dropped by the external merge"
    )


@pytest.mark.xfail(
    strict=True,
    reason="F104 — same root cause as the test above; see crag.py.",
)
@pytest.mark.asyncio
async def test_wired_crag_merge_holds_the_flatten_invariant(wired_crag):
    """``results == flatten(source_breakdown)`` asserted against a real merge.

    The envelope tests assert this against hand-built dicts, which cannot
    fail: they seed both sides from the same literal.
    """
    from core.agents.query_agent import agent_query_full

    wired_crag([
        {"content": "IRS 83(b) overview", "title": "83(b)", "url": "https://x/1",
         "source_name": "irs", "relevance": 0.9},
    ])

    with patch(
        "core.agents.query_agent.agent_query",
        new=AsyncMock(return_value=_legacy_kb_result()),
    ):
        out = await agent_query_full(query="my vesting cliff", external_augmentation=True)

    breakdown = out["source_breakdown"]
    assert len(out["results"]) == sum(len(v) for v in breakdown.values())
    assert len(breakdown["kb"]) == 2
    assert len(breakdown["external"]) == 1


@pytest.mark.asyncio
async def test_wired_crag_fires_and_appends_the_external_hit(wired_crag):
    """Control for the two above: the registry IS reached and its rows do
    arrive. Without this, a merge that returned the KB results untouched by
    never firing at all would look like a fix."""
    from core.agents.query_agent import agent_query_full

    registry = wired_crag([
        {"content": "IRS 83(b) overview", "title": "83(b)", "url": "https://x/1",
         "source_name": "irs", "relevance": 0.9},
    ])

    with patch(
        "core.agents.query_agent.agent_query",
        new=AsyncMock(return_value=_legacy_kb_result()),
    ):
        out = await agent_query_full(query="my vesting cliff", external_augmentation=True)

    assert registry.calls == 1
    external = [r for r in out["results"] if r.get("source_type") == "external"]
    assert len(external) == 1
    assert external[0]["source_url"] == "https://x/1"


@pytest.mark.asyncio
async def test_wired_crag_does_not_fire_on_a_strong_kb_hit(wired_crag):
    """The gate's other half: a confident KB answer must not pay for network
    I/O, and must come back byte-identical."""
    from core.agents.query_agent import agent_query_full

    registry = wired_crag([{"content": "unused", "source_name": "irs"}])
    strong = _legacy_kb_result()
    for row in strong["results"]:
        row["relevance"] = 0.95

    with patch(
        "core.agents.query_agent.agent_query",
        new=AsyncMock(return_value=strong),
    ):
        out = await agent_query_full(query="my vesting cliff", external_augmentation=True)

    assert registry.calls == 0
    assert [r["artifact_id"] for r in out["results"]] == ["a1", "a2"]
