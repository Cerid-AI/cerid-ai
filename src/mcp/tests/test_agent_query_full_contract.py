# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Behavioral parity guard for the canonical retrieval entry ``agent_query_full``.

Phase 1 made ``core.agents.query_agent.agent_query_full`` the single path every
surface (REST /agent/query, MCP pkb_agent_query, A2A, custom agents, /query,
/sdk/v1/search) routes through. The retrieval-import-boundary gate enforces the
*structural* invariant (no new bypass importers); these tests lock the
*behavioral* contract so a refactor can't silently drop a stage:

- ``exclude_packs`` is forwarded to the core retrieval primitive (RPB-2 fix).
- provenance (``source_type`` / ``pack_id``) survives the wrapper.
- the KB-only ``low_confidence`` signal is stamped (B2a invariant).
- the conversation-only stub fires when KB is disabled.
- external augmentation is a no-op when the CRAG registry is unwired (so the
  path is safe in tests / the public mirror / any unconfigured deployment).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


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
    # strong KB (0.9 >= 0.4 default threshold) → not low-confidence
    assert out["low_confidence"] is False


@pytest.mark.asyncio
async def test_low_confidence_stamped_on_weak_kb():
    from core.agents.query_agent import agent_query_full

    fake = {"results": [{"relevance": 0.1}], "sources": [], "confidence": 0.1}
    with patch("core.agents.query_agent.agent_query", new=AsyncMock(return_value=fake)):
        out = await agent_query_full(query="q", external_augmentation=False)

    assert out["low_confidence"] is True


@pytest.mark.asyncio
async def test_conversation_only_stub_when_kb_disabled():
    from core.agents.query_agent import agent_query_full

    # No agent_query call should happen when KB is gated off.
    with patch("core.agents.query_agent.agent_query", new=AsyncMock()) as mock_aq:
        out = await agent_query_full(query="q", kb_enabled=False, external_augmentation=False)

    mock_aq.assert_not_called()
    assert out["strategy"] == "conversation_only"
    assert out["source_status"] == {"kb": "disabled"}
    assert out["low_confidence"] is True  # empty results → low confidence


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

    # Gate would fire (weak KB) but firing is a no-op → result unchanged + stamped.
    assert out["sources"] == [{"source_type": "kb"}]
    assert out["low_confidence"] is True
