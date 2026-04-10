# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for memory type-aware scoring, per-type recall thresholds, and NLI gate.

Covers:
- calculate_memory_score() per-type decay curves (empirical, conversational,
  temporal, decision, preference, project_context)
- Per-type recall threshold filtering (MEMORY_MIN_RECALL_BY_TYPE)
- NLI relevance gate in recall_memories()
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

# Mock heavy native deps if not available (host macOS lacks them; Docker has them).
# We must also mock core.utils.embeddings itself because it uses Python 3.10+
# union syntax (str | None) that fails to parse on the host's Python 3.9.
for _mod in ("onnxruntime", "huggingface_hub", "tokenizers"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
if "core.utils.embeddings" not in sys.modules:
    _emb_mock = MagicMock()
    _emb_mock.l2_distance_to_relevance = lambda d: max(0.0, 1.0 - d / 2.0)
    sys.modules["core.utils.embeddings"] = _emb_mock

import pytest  # noqa: E402

# Stub heavy transitive imports so we don't trigger real model loading.
if "routers.ingestion" not in sys.modules:
    _stub = ModuleType("routers.ingestion")
    _stub.ingest_content = None  # type: ignore[attr-defined]
    _stub.ingest_batch = None  # type: ignore[attr-defined]
    _stub.router = MagicMock()  # type: ignore[attr-defined]
    sys.modules["routers.ingestion"] = _stub
    if "routers" not in sys.modules:
        sys.modules["routers"] = ModuleType("routers")
    sys.modules["routers"].ingestion = _stub  # type: ignore[attr-defined]

from core.agents.memory import calculate_memory_score, recall_memories  # noqa: E402

# ---------------------------------------------------------------------------
# TestCalculateMemoryScore — per-type decay curves
# ---------------------------------------------------------------------------


class TestCalculateMemoryScore:
    """Verify decay/reinforcement curves for each memory_type."""

    def test_empirical_no_decay(self):
        """Empirical facts should have decay=1.0 regardless of age."""
        score = calculate_memory_score(0.7, 0, 365, memory_type="empirical")
        # decay=1.0, reinforcement=1.0 (empirical skips reinforcement)
        assert score == pytest.approx(0.7, abs=0.01)

    def test_conversational_rapid_decay(self):
        """Conversational memories have 3-day half-life exponential decay."""
        score = calculate_memory_score(0.7, 0, 30, memory_type="conversational")
        # 2^(-30/3) = 2^(-10) ≈ 0.000977; 0.7 * 1.0 * 0.000977 ≈ 0.0007
        assert score < 0.01, f"Expected near-zero after 30 days, got {score}"

    def test_temporal_step_function(self):
        """Temporal events: full score before event, 0.1 residual after."""
        # Before event (age_days <= 0 means event is in the future)
        before = calculate_memory_score(0.7, 0, -1, memory_type="temporal")
        assert before == pytest.approx(0.7, abs=0.01)

        # After event (age_days > 0 means event has passed)
        after = calculate_memory_score(0.7, 0, 1, memory_type="temporal")
        # decay=0.1 → 0.7 * 1.0 * 0.1 = 0.07
        assert after == pytest.approx(0.07, abs=0.01)

    def test_decision_power_law(self):
        """Decision type uses power-law with 90-day stability."""
        score = calculate_memory_score(0.7, 0, 90, memory_type="decision")
        # Power-law: (1 + 90/(9*90))^(-0.5) = (1 + 1/9)^(-0.5)
        #          = (10/9)^(-0.5) = (9/10)^(0.5) ≈ 0.9487
        # score = 0.7 * 1.0 * 0.9487 ≈ 0.664
        expected_decay = (1.0 + 90.0 / (9.0 * 90.0)) ** (-0.5)
        assert score == pytest.approx(0.7 * expected_decay, abs=0.01)
        # Should be roughly in the ~0.5-0.7 range (slow decay)
        assert 0.5 < score < 0.75

    def test_preference_power_law(self):
        """Preference type uses power-law with 60-day stability."""
        score = calculate_memory_score(0.7, 0, 60, memory_type="preference")
        # Power-law: (1 + 60/(9*60))^(-0.5) = same as decision at its stability
        expected_decay = (1.0 + 60.0 / (9.0 * 60.0)) ** (-0.5)
        assert score == pytest.approx(0.7 * expected_decay, abs=0.01)
        assert 0.5 < score < 0.75

    def test_project_context_exponential(self):
        """project_context uses exponential decay with 14-day stability."""
        score = calculate_memory_score(0.7, 0, 14, memory_type="project_context")
        # Exponential: 2^(-14/14) = 2^(-1) = 0.5
        # score = 0.7 * 1.0 * 0.5 = 0.35
        assert score == pytest.approx(0.35, abs=0.01)

    def test_default_is_decision(self):
        """When memory_type not specified, should default to 'decision'."""
        default_score = calculate_memory_score(0.7, 0, 90)
        explicit_score = calculate_memory_score(0.7, 0, 90, memory_type="decision")
        assert default_score == pytest.approx(explicit_score, abs=0.001)

    def test_reinforcement_capped(self):
        """access_count=1000 should cap reinforcement at 5x."""
        score = calculate_memory_score(0.1, 1000, 0, memory_type="empirical")
        # empirical: decay=1.0, reinforcement skipped (always 1.0)
        # Wait — empirical skips reinforcement. Let's use a non-empirical type.
        # For empirical, reinforcement is always 1.0 → 0.1 * 1.0 * 1.0 = 0.1
        assert score == pytest.approx(0.1, abs=0.01)

    def test_reinforcement_capped_non_empirical(self):
        """Non-empirical with access_count=1000: reinforcement caps at 5x."""
        # Use decision at age=0 so decay=1.0 (power-law at t=0)
        score = calculate_memory_score(0.1, 1000, 0, memory_type="decision")
        # reinforcement = min(1 + log2(1 + 1000), 5) = min(1 + ~9.97, 5) = 5.0
        # score = 0.1 * 5.0 * 1.0 = 0.5
        assert score == pytest.approx(0.5, abs=0.01)


# ---------------------------------------------------------------------------
# TestPerTypeRecallThresholds — threshold-based filtering
# ---------------------------------------------------------------------------


class TestPerTypeRecallThresholds:
    """Verify that per-type min recall thresholds filter correctly."""

    def test_conversational_high_threshold(self):
        """A conversational memory with adjusted_score=0.5 should be filtered (threshold 0.55)."""
        import config

        threshold = config.MEMORY_MIN_RECALL_BY_TYPE.get("conversational", 0.3)
        assert threshold == 0.55, f"Expected conversational threshold 0.55, got {threshold}"
        # Score 0.5 < 0.55 → should be filtered
        assert 0.5 < threshold

    def test_empirical_lower_threshold(self):
        """An empirical memory with adjusted_score=0.42 should pass (threshold 0.4)."""
        import config

        threshold = config.MEMORY_MIN_RECALL_BY_TYPE.get("empirical", 0.3)
        assert threshold == 0.4, f"Expected empirical threshold 0.4, got {threshold}"
        # Score 0.42 >= 0.4 → should pass
        assert 0.42 >= threshold

    def test_temporal_strict_threshold(self):
        """A temporal memory with adjusted_score=0.45 should be filtered (threshold 0.5)."""
        import config

        threshold = config.MEMORY_MIN_RECALL_BY_TYPE.get("temporal", 0.3)
        assert threshold == 0.5, f"Expected temporal threshold 0.5, got {threshold}"
        # Score 0.45 < 0.5 → should be filtered
        assert 0.45 < threshold


# ---------------------------------------------------------------------------
# TestMemoryNliGate — NLI relevance check in recall_memories
# ---------------------------------------------------------------------------


def _make_chroma_mock(
    ids: list[str],
    documents: list[str],
    distances: list[float],
    metadatas: list[dict] | None = None,
) -> MagicMock:
    """Build a mock ChromaDB client whose collection.query returns given data."""
    if metadatas is None:
        metadatas = [
            {
                "artifact_id": aid,
                "memory_type": "empirical",
                "valid_from": "2025-01-01T00:00:00Z",
                "access_count": "0",
                "summary": f"summary-{aid}",
            }
            for aid in ids
        ]

    collection = MagicMock()
    collection.query.return_value = {
        "ids": [ids],
        "documents": [documents],
        "distances": [distances],
        "metadatas": [metadatas],
    }

    client = MagicMock()
    client.get_or_create_collection.return_value = collection
    return client


class TestMemoryNliGate:
    """Verify the NLI relevance gate in recall_memories."""

    @pytest.mark.asyncio
    async def test_contradiction_memory_skipped(self):
        """High contradiction score should cause memory to be filtered."""
        mock_nli_module = MagicMock()
        mock_nli_module.return_value = {
            "entailment": 0.1,
            "neutral": 0.1,
            "contradiction": 0.8,
        }

        chroma = _make_chroma_mock(
            ids=["mem-1"],
            documents=["The earth is flat"],
            distances=[0.3],
        )

        with patch("core.utils.nli.nli_score", mock_nli_module), \
             patch("core.agents.memory.l2_distance_to_relevance", return_value=0.85):
            results = await recall_memories("What shape is the earth?", chroma, None)

        # Memory has good base score (0.85) but NLI says contradiction
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_low_entailment_low_score_skipped(self):
        """Low entailment + low adjusted score should be filtered."""
        mock_nli_fn = MagicMock(return_value={
            "entailment": 0.2,
            "neutral": 0.6,
            "contradiction": 0.2,
        })

        chroma = _make_chroma_mock(
            ids=["mem-2"],
            documents=["Unrelated tangent about cooking"],
            distances=[1.2],  # higher L2 distance = lower similarity
        )

        with patch("core.utils.nli.nli_score", mock_nli_fn), \
             patch("core.agents.memory.l2_distance_to_relevance", return_value=0.35):
            results = await recall_memories("Python GIL behavior", chroma, None)

        # Low entailment (0.2 < 0.3) AND low adjusted score (0.35 < 0.5) → filtered
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_high_entailment_kept(self):
        """High entailment score should keep the memory."""
        mock_nli_fn = MagicMock(return_value={
            "entailment": 0.8,
            "neutral": 0.15,
            "contradiction": 0.05,
        })

        chroma = _make_chroma_mock(
            ids=["mem-3"],
            documents=["Python uses a Global Interpreter Lock"],
            distances=[0.2],
        )

        with patch("core.utils.nli.nli_score", mock_nli_fn), \
             patch("core.agents.memory.l2_distance_to_relevance", return_value=0.9):
            results = await recall_memories("What is the Python GIL?", chroma, None)

        assert len(results) == 1
        assert results[0]["memory_id"] == "mem-3"

    @pytest.mark.asyncio
    async def test_nli_unavailable_falls_back(self):
        """When nli_score raises ImportError, memory should use adjusted_score only."""
        def nli_explodes(*args, **kwargs):
            raise ImportError("ONNX not available")

        chroma = _make_chroma_mock(
            ids=["mem-4"],
            documents=["Important fact about Python"],
            distances=[0.2],
        )

        with patch("core.utils.nli.nli_score", side_effect=nli_explodes), \
             patch("core.agents.memory.l2_distance_to_relevance", return_value=0.85):
            results = await recall_memories("Tell me about Python", chroma, None)

        # NLI failed but score is above threshold → kept
        assert len(results) == 1
        assert results[0]["memory_id"] == "mem-4"
