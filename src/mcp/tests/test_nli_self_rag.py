# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for NLI-based claim coverage in Self-RAG _assess_claims()."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture()
def _mock_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide minimal config stubs for _assess_claims."""
    import config

    monkeypatch.setattr(config, "DOMAINS", ["knowledge", "code", "conversations"])


def _fake_nli(entailment: float, contradiction: float, neutral: float) -> dict:
    probs = {"entailment": entailment, "contradiction": contradiction, "neutral": neutral}
    best = max(probs, key=probs.get)  # type: ignore[arg-type]
    return {**probs, "label": best}


# ── Test 1: Paraphrased support → covered=True via NLI entailment ──────────

@pytest.mark.asyncio()
@pytest.mark.usefixtures("_mock_config")
async def test_nli_entailment_marks_claim_covered() -> None:
    """When NLI returns high entailment, the claim should be marked covered."""
    fake_results = [
        {"relevance": 0.4, "content": "Python 3.12 was released in October 2023.", "filename": "notes.md"},
    ]

    with (
        patch(
            "core.agents.query_agent.multi_domain_query",
            new_callable=AsyncMock,
            return_value=fake_results,
        ),
        patch(
            "core.utils.nli.nli_score",
            return_value=_fake_nli(0.85, 0.05, 0.10),
        ),
    ):
        from core.agents.self_rag import _assess_claims

        assessments = await _assess_claims(
            claims=["Python 3.12 came out in Oct 2023"],
            chroma_client=None,
            threshold=0.7,
        )

    assert len(assessments) == 1
    a = assessments[0]
    assert a["covered"] is True
    assert a["contradicted"] is False
    assert a["nli_entailment"] == 0.85


# ── Test 2: KB contradicts claim → contradicted=True ────────────────────────

@pytest.mark.asyncio()
@pytest.mark.usefixtures("_mock_config")
async def test_nli_contradiction_marks_claim_contradicted() -> None:
    """When NLI returns high contradiction, the claim should be flagged."""
    fake_results = [
        {"relevance": 0.6, "content": "The project deadline is March 15.", "filename": "plan.md"},
    ]

    with (
        patch(
            "core.agents.query_agent.multi_domain_query",
            new_callable=AsyncMock,
            return_value=fake_results,
        ),
        patch(
            "core.utils.nli.nli_score",
            return_value=_fake_nli(0.05, 0.80, 0.15),
        ),
    ):
        from core.agents.self_rag import _assess_claims

        assessments = await _assess_claims(
            claims=["The project deadline is June 30"],
            chroma_client=None,
            threshold=0.7,
        )

    assert len(assessments) == 1
    a = assessments[0]
    assert a["covered"] is False
    assert a["contradicted"] is True
    assert a["nli_contradiction"] == 0.80


# ── Test 3: NLI unavailable → falls back to similarity ─────────────────────

@pytest.mark.asyncio()
@pytest.mark.usefixtures("_mock_config")
async def test_nli_unavailable_falls_back_to_similarity() -> None:
    """When NLI import fails, coverage should fall back to max_sim >= threshold."""
    fake_results = [
        {"relevance": 0.85, "content": "Some KB content", "filename": "doc.md"},
    ]

    def _nli_raise(*_args: object, **_kwargs: object) -> None:
        raise ImportError("onnxruntime not available")

    with (
        patch(
            "core.agents.query_agent.multi_domain_query",
            new_callable=AsyncMock,
            return_value=fake_results,
        ),
        patch(
            "core.utils.nli.nli_score",
            side_effect=_nli_raise,
        ),
    ):
        from core.agents.self_rag import _assess_claims

        assessments = await _assess_claims(
            claims=["Some claim matching KB well"],
            chroma_client=None,
            threshold=0.7,
        )

    assert len(assessments) == 1
    a = assessments[0]
    # NLI failed → best_nli stays default (entailment=0, label="neutral")
    # Fallback: max_sim (0.85) >= threshold (0.7) → covered
    assert a["covered"] is True
    # contradiction stays default 0.0, below 0.6 threshold
    assert a["contradicted"] is False
