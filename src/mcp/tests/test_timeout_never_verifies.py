# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A claim the verification deadline cut short is never marked verified (audit F115).

The timeout fallback marked a claim verified when the batch pre-fetch had found
KB text at retrieval similarity 0.65 or more: no entailment check, and no
anti-circular filter, on a corpus where unrelated chunks reach 0.7. The
generator is real; claim extraction, the KB pre-fetch and a verify_claim that
never settles are faked.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

import core.agents.hallucination.streaming as streaming
from tests.integration.test_e1_streaming_timeout_settlement import _CLAIM, _RESPONSE, _CaptureRedis


async def _timed_out_event(monkeypatch, kb_relevance: float) -> dict:
    async def extract(response_text, user_query=None):
        return [_CLAIM], "heuristic"

    async def prefetch(query, **_k):
        return [{"content": f"{_CLAIM} Related text.", "relevance": kb_relevance,
                 "artifact_id": "a1", "domain": "general", "source": "sky.md"}]

    async def never_settles(*_a, **_k):
        await asyncio.sleep(1.0)
        return {"status": "verified", "similarity": 0.9}

    monkeypatch.setattr(streaming, "extract_claims", extract)
    monkeypatch.setattr(streaming, "_extract_claims_heuristic", lambda t: [_CLAIM])
    monkeypatch.setattr(streaming, "_resolve_pronouns_heuristic", lambda c, *a, **k: c)
    monkeypatch.setattr("core.agents.query_agent.lightweight_kb_query", prefetch)
    monkeypatch.setattr(streaming.config, "STREAMING_TOTAL_TIMEOUT", 0.05, raising=False)
    monkeypatch.setattr(streaming, "verify_claim", never_settles)

    events = [ev async for ev in streaming.verify_response_streaming(
        response_text=_RESPONSE, conversation_id="f115", chroma_client=MagicMock(),
        neo4j_driver=MagicMock(), redis_client=_CaptureRedis(),
    )]
    return [e for e in events if e.get("type") == "claim_verified"][-1]


@pytest.mark.asyncio
async def test_related_kb_text_at_timeout_leaves_the_claim_uncertain(monkeypatch):
    ev = await _timed_out_event(monkeypatch, kb_relevance=0.8)

    assert ev["verification_method"] == "kb_only_timeout", "the KB evidence path must be the one exercised"
    assert ev["status"] == "uncertain"
