# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A gated answer must say it was gated.

``pkb_answer_with_citations`` streams synthesis through the inline NLI gate
when ``ENABLE_INLINE_NLI_GATING`` is on, but called ``gated_synthesis``
without an ``on_suppress`` callback. A suppressed sentence returned ``""``
from the gate and was skipped, leaving a ``logger.debug`` line as the only
trace: the reader got a silently truncated answer, and nothing downstream
could tell an over-aggressive gate from a terse model.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

_CONTRADICTED = "The retrieval pipeline does no reranking at all."
_SUPPORTED = "The pipeline reranks candidate chunks with a cross-encoder."


def _envelope_results():
    return [
        {
            "content": "The Cerid AI retrieval pipeline reranks candidate chunks "
                       "with a cross-encoder model before synthesis.",
            "artifact_id": "art-1",
            "chunk_id": "chunk-1",
        },
    ]


def _patch_gated_answer_path(monkeypatch):
    import app.mcp_tools.retrieval as retrieval_mod
    from core.retrieval.surface_router import SurfaceRoute

    route = SurfaceRoute(
        primary="vector",
        surfaces=["vector"],
        intent="factual",
        confidence=1.0,
        matched_entity_hint=None,
    )
    monkeypatch.setattr("core.retrieval.surface_router.route", lambda _q: route)
    monkeypatch.setattr(
        "core.agents.query_agent.agent_query",
        AsyncMock(return_value={
            "results": _envelope_results(),
            "context": "The pipeline reranks chunks with a cross-encoder.",
            "total_results": 1,
        }),
    )
    monkeypatch.setattr(
        "core.agents.hallucination.extraction.extract_claims",
        AsyncMock(return_value=([], "stub")),
    )
    monkeypatch.setattr(retrieval_mod, "get_redis", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(retrieval_mod, "get_chroma", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(retrieval_mod, "get_neo4j", MagicMock(return_value=MagicMock()))

    async def _stream(*_a, **_kw):
        yield f"{_SUPPORTED} "
        yield f"{_CONTRADICTED} "

    monkeypatch.setattr("core.utils.internal_llm.call_internal_llm_stream", _stream)

    async def _nli(_premise, hypothesis):
        if hypothesis.strip().startswith("The retrieval pipeline does no reranking"):
            return {"contradiction": 0.95, "entailment": 0.01, "neutral": 0.04}
        return {"contradiction": 0.01, "entailment": 0.9, "neutral": 0.09}

    monkeypatch.setattr(
        "core.agents.hallucination.inline_gate.nli_score_async", _nli,
    )
    return retrieval_mod


@pytest.mark.asyncio
async def test_a_suppressed_sentence_is_reported_to_the_caller(monkeypatch):
    import config

    retrieval_mod = _patch_gated_answer_path(monkeypatch)
    monkeypatch.setattr(config, "ENABLE_INLINE_NLI_GATING", True, raising=False)

    out = await retrieval_mod.pkb_answer_with_citations("how does reranking work?")

    # The gate did its job on the answer text...
    assert _SUPPORTED in out["answer"]
    assert "does no reranking" not in out["answer"]
    # ...and the caller can tell that it did.
    gate = out["inline_gate"]
    assert gate["enabled"] is True
    assert gate["suppressed_count"] == 1
    assert any(
        "does no reranking" in entry["sentence"]
        for entry in gate["suppressed_sentences"]
    )
    assert gate["suppressed_sentences"][0]["contradiction"] >= 0.9


@pytest.mark.asyncio
async def test_an_ungated_answer_reports_no_suppression(monkeypatch):
    import config

    retrieval_mod = _patch_gated_answer_path(monkeypatch)
    monkeypatch.setattr(config, "ENABLE_INLINE_NLI_GATING", True, raising=False)

    async def _nli_all_clear(_premise, _hypothesis):
        return {"contradiction": 0.0, "entailment": 0.99, "neutral": 0.01}

    monkeypatch.setattr(
        "core.agents.hallucination.inline_gate.nli_score_async", _nli_all_clear,
    )

    out = await retrieval_mod.pkb_answer_with_citations("how does reranking work?")

    assert out["inline_gate"]["enabled"] is True
    assert out["inline_gate"]["suppressed_count"] == 0
    assert out["inline_gate"]["suppressed_sentences"] == []
    assert _CONTRADICTED in out["answer"]


@pytest.mark.asyncio
async def test_gate_off_is_declared_not_omitted(monkeypatch):
    """"The gate never ran" and "the gate ran and found nothing" differ."""
    import config

    retrieval_mod = _patch_gated_answer_path(monkeypatch)
    monkeypatch.setattr(config, "ENABLE_INLINE_NLI_GATING", False, raising=False)
    monkeypatch.setattr(
        "core.utils.internal_llm.call_internal_llm",
        AsyncMock(return_value=f"{_SUPPORTED} {_CONTRADICTED}"),
    )

    out = await retrieval_mod.pkb_answer_with_citations("how does reranking work?")

    assert out["inline_gate"]["enabled"] is False
    assert out["inline_gate"]["suppressed_count"] == 0


@pytest.mark.asyncio
async def test_the_no_sources_reply_carries_the_same_gate_shape(monkeypatch):
    """Every answer shape reports the gate, so clients need no key guard."""
    import config

    retrieval_mod = _patch_gated_answer_path(monkeypatch)
    monkeypatch.setattr(config, "ENABLE_INLINE_NLI_GATING", True, raising=False)
    monkeypatch.setattr(
        "core.agents.query_agent.agent_query",
        AsyncMock(return_value={"results": [], "context": "", "total_results": 0}),
    )

    out = await retrieval_mod.pkb_answer_with_citations("nothing matches this")

    assert out["inline_gate"] == {
        "enabled": False,
        "suppressed_count": 0,
        "suppressed_sentences": [],
    }
