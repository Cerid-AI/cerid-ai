# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A verdict says whether it rests on the answer's own sources (ruled 2026-10-10).

A claim verified against a KB document the answer was generated from still
counts: the answer is faithful to the knowledge base. It is labelled
``circular_source`` so it reads "KB verified", not "independently verified".
The label follows the evidence of THIS answer, so a cached verdict reached for
another answer is relabelled, never trusted.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.agents.hallucination import verification
from core.agents.hallucination.verification import label_answer_source, verify_claim

VERDICT = {"claim": "Water boils at 100 C at sea level.", "status": "verified",
           "verification_method": "kb_nli", "source_artifact_id": "doc-a"}


def test_evidence_among_the_answer_sources_is_kb_verified():
    assert label_answer_source(VERDICT, ["doc-a", "doc-b"])["circular_source"] is True


def test_other_evidence_is_independent():
    assert "circular_source" not in label_answer_source(VERDICT, ["doc-z"])
    assert "circular_source" not in label_answer_source(VERDICT, None)
    assert "circular_source" not in label_answer_source({**VERDICT, "source_artifact_id": ""}, ["doc-a"])


def test_a_label_from_another_answer_is_dropped_and_the_cached_verdict_is_untouched():
    cached = {**VERDICT, "circular_source": True}

    out = label_answer_source(cached, ["doc-z"])

    assert "circular_source" not in out
    assert cached["circular_source"] is True


@pytest.mark.asyncio
async def test_verify_claim_labels_whatever_path_produced_the_verdict():
    with patch.object(verification, "_verify_claim", new=AsyncMock(return_value=dict(VERDICT))):
        kb = await verify_claim("c", None, None, None, source_artifact_ids=["doc-a"])
        independent = await verify_claim("c", None, None, None, source_artifact_ids=["doc-b"])

    assert kb["circular_source"] is True
    assert "circular_source" not in independent


@pytest.mark.asyncio
async def test_the_batch_check_threads_the_answer_sources(monkeypatch):
    from core.agents.hallucination import streaming

    seen: list[list[str] | None] = []

    async def fake_verify(*_a, source_artifact_ids=None, **_k):
        seen.append(source_artifact_ids)
        return dict(VERDICT)

    monkeypatch.setattr(streaming, "verify_claim", fake_verify)
    monkeypatch.setattr(streaming, "extract_claims", AsyncMock(return_value=([VERDICT["claim"]], "heuristic")))
    monkeypatch.setattr(streaming.config, "HALLUCINATION_MIN_RESPONSE_LENGTH", 1, raising=False)

    await streaming.check_hallucinations(
        "Water boils at 100 C at sea level, which is well known.", "conv-1", None, None, None,
        persist_report=False, source_artifact_ids=["doc-a"],
    )

    assert seen == [["doc-a"]]


@pytest.mark.asyncio
@pytest.mark.parametrize(("answer_sources", "kb_verified"), [(["doc-a"], True), (["doc-z"], False)])
async def test_a_claim_grounded_only_in_an_answer_source_is_kb_verified_not_sent_to_the_web(
    answer_sources, kb_verified, mock_chroma, mock_neo4j, mock_redis,
):
    """Before the ruling, an answer whose every top KB match was one of its own
    sources escalated to external verification, and those sources lost 0.15
    relevance. Retrieval, memories, NLI and the external verifier are faked;
    the verification pipeline is real."""
    kb_row = {"content": "At sea level, water boils at 100 degrees Celsius.", "relevance": 0.82,
              "artifact_id": "doc-a", "filename": "water.md", "domain": "general"}
    external = AsyncMock(return_value={"status": "verified", "confidence": 0.9, "verification_method": "web_search"})
    with (
        patch("core.agents.query_agent.lightweight_kb_query", new=AsyncMock(return_value=[kb_row])),
        patch.object(verification, "_query_memories", new=AsyncMock(return_value=[])),
        patch.object(verification, "_verify_claim_externally", new=external),
        patch("core.utils.nli.nli_score_async", new=AsyncMock(return_value={
            "entailment": 0.95, "contradiction": 0.01, "neutral": 0.04, "label": "entailment"})),
        patch("core.utils.claim_cache.get_cached_verdict", return_value=None, create=True),
    ):
        result = await verify_claim(
            "Water boils at 100 degrees Celsius at sea level.",
            mock_chroma[0], mock_neo4j[0], mock_redis, source_artifact_ids=answer_sources,
        )

    external.assert_not_awaited()
    assert result["status"] == "verified"
    assert result["source_artifact_id"] == "doc-a"
    assert bool(result.get("circular_source")) is kb_verified
