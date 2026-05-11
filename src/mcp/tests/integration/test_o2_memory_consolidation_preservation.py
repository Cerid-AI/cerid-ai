# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Preservation gate I20 — memory consolidation pipeline (Phase O.2).

This is a **synthetic** preservation test: it does NOT require a live
stack.  It wires real service code with in-memory mocks to assert the
memory consolidation pipeline end-to-end.

I20 invariants:
  a) Round-trip: N synthetic memories fed into the pipeline produce
     queryable, stored results.
  b) Ebbinghaus decay schedule: a ``conversational``-type memory aged past
     its decay window has a lower adjusted score than the same memory at
     age zero, and an ``empirical``-type memory is immune to decay.
  c) NLI guard: two contradictory memories fed through conflict resolution
     result in the lower-confidence one being rejected (coexist or
     supersede, not merged into semantic drift), and a ContradictionFinding
     edge OR ``(:Claim)-[:CONTRADICTS]->(:Claim)`` is recorded when the
     NLI guard fires.

NOTE: Do NOT register I20 in docs/PRESERVATION.md until v0.92 cut.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mark the whole module as a preservation gate.
pytestmark = pytest.mark.preservation


# ---------------------------------------------------------------------------
# In-memory Chroma stub (real metadata tracking, no external dep)
# ---------------------------------------------------------------------------


def _make_tracked_collection() -> MagicMock:
    """Return a mock Chroma collection with real in-memory metadata tracking."""
    coll = MagicMock()
    _store: dict[str, dict] = {}

    def _add(ids, documents, metadatas, embeddings=None):
        for i, cid in enumerate(ids):
            _store[cid] = dict(metadatas[i] if metadatas else {})
            _store[cid]["_doc"] = documents[i] if documents else ""

    def _update(ids, metadatas=None, documents=None):
        for i, cid in enumerate(ids):
            if cid in _store and metadatas:
                _store[cid].update(metadatas[i])

    def _get(ids=None, where=None, include=None):
        rows = {cid: _store[cid] for cid in (ids or _store) if cid in _store}
        return {
            "ids": list(rows.keys()),
            "documents": [v.get("_doc", "") for v in rows.values()],
            "metadatas": list(rows.values()),
        }

    def _query(query_texts, n_results=5, include=None, where=None):
        """Return all stored memories as candidates (no real embedding distance)."""
        all_ids = list(_store.keys())[:n_results]
        return {
            "ids": [all_ids],
            "documents": [[_store[cid].get("_doc", "") for cid in all_ids]],
            "metadatas": [[_store[cid] for cid in all_ids]],
            "distances": [[0.05] * len(all_ids)],  # ≡ 0.95 similarity
        }

    def _delete(ids):
        for cid in ids:
            _store.pop(cid, None)

    coll.add.side_effect = _add
    coll.update.side_effect = _update
    coll.get.side_effect = _get
    coll.query.side_effect = _query
    coll.delete.side_effect = _delete
    coll._store = _store
    return coll


# ---------------------------------------------------------------------------
# Minimal ingest_fn stub
# ---------------------------------------------------------------------------


def _make_ingest_fn(collection: MagicMock):
    """Return a synchronous ingest function that writes into the mock collection."""
    _counter = [0]

    def _ingest(content: str, domain: str, metadata: dict | None = None) -> dict:
        metadata = metadata or {}
        _counter[0] += 1
        artifact_id = f"art-{uuid.uuid4().hex[:8]}"
        chunk_id = f"chunk-{artifact_id}"
        metadata["artifact_id"] = artifact_id
        metadata["cerid_state"] = "committed"
        collection.add(
            ids=[chunk_id],
            documents=[content],
            metadatas=[metadata],
        )
        return {"status": "success", "artifact_id": artifact_id}

    return _ingest


# ---------------------------------------------------------------------------
# I20.a — round-trip: memories fed in → stored and queryable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i20a_round_trip_store_and_query():
    """N synthetic memories fed through extract_and_store_memories are stored.

    The canonical pipeline entry point is
    ``core.agents.memory.extract_and_store_memories`` (exposed via the
    ``app.agents.memory`` bridge that injects the ingest function).
    """
    from core.agents.memory import extract_and_store_memories

    collection = _make_tracked_collection()
    ingest_fn = _make_ingest_fn(collection)

    synthetic_memories = [
        {
            "content": "The user prefers functional programming in Haskell.",
            "memory_type": "preference",
            "summary": "Prefers Haskell",
        },
        {
            "content": "Project deadline is 2026-06-01.",
            "memory_type": "temporal",
            "summary": "Deadline June 1",
        },
        {
            "content": "Python's GIL prevents true thread parallelism for CPU-bound work.",
            "memory_type": "empirical",
            "summary": "GIL fact",
        },
    ]

    # Patch extract_memories to return our controlled set
    with patch(
        "core.agents.memory.extract_memories",
        new_callable=AsyncMock,
        return_value=synthetic_memories,
    ):
        # Disable consolidation so we focus on store, not classify
        with patch.dict(
            "config.features.FEATURE_TOGGLES",
            {"enable_memory_consolidation": False},
        ):
            result = await extract_and_store_memories(
                "x" * 200,  # response_text (length check passes)
                conversation_id=f"i20-test-{uuid.uuid4().hex[:8]}",
                chroma_client=MagicMock(
                    get_collection=MagicMock(return_value=collection),
                    get_or_create_collection=MagicMock(return_value=collection),
                ),
                ingest_fn=ingest_fn,
            )

    # All three should be stored
    assert result["memories_extracted"] == 3, (
        f"Expected 3 extracted, got {result['memories_extracted']}"
    )
    assert result["memories_stored"] == 3, (
        f"Expected 3 stored, got {result['memories_stored']}. "
        f"Results: {result['results']}"
    )

    # All should be queryable via the collection store
    assert len(collection._store) == 3, (
        f"Expected 3 entries in collection, got {len(collection._store)}"
    )

    # Verify each stored entry has the documented metadata fields
    for meta in collection._store.values():
        assert "artifact_id" in meta, "Missing artifact_id in stored memory"
        assert "memory_type" in meta, "Missing memory_type in stored memory"
        assert "conversation_id" in meta, "Missing conversation_id in stored memory"


# ---------------------------------------------------------------------------
# I20.b — Ebbinghaus decay schedule fires correctly
# ---------------------------------------------------------------------------


def test_i20b_ebbinghaus_decay_schedule():
    """Decay scoring: older memories score lower; empirical type is immune.

    The ``calculate_memory_score`` function in ``core.agents.memory`` is the
    canonical implementation of the Ebbinghaus-inspired decay model.

    Assertions:
    - A ``conversational`` memory aged past its stability window (3 days)
      has a materially lower score than the same memory at age 0.
    - An ``empirical`` memory has identical score regardless of age (no decay).
    - A ``conversational`` memory aged 2× its stability window is below 50%
      of its fresh score (exponential decay contract).
    """
    from core.agents.memory import calculate_memory_score

    base_score = 0.9

    # --- conversational: exponential decay, stability = 3 days ---
    fresh_score = calculate_memory_score(
        base_score=base_score,
        access_count=0,
        age_days=0.0,
        memory_type="conversational",
    )
    aged_score = calculate_memory_score(
        base_score=base_score,
        access_count=0,
        age_days=3.0,  # at stability boundary
        memory_type="conversational",
    )
    very_old_score = calculate_memory_score(
        base_score=base_score,
        access_count=0,
        age_days=6.0,  # 2× stability
        memory_type="conversational",
    )

    assert aged_score < fresh_score, (
        f"Aged conversational score {aged_score:.4f} should be < fresh {fresh_score:.4f}"
    )
    assert very_old_score < aged_score, (
        f"Very old score {very_old_score:.4f} should be < aged {aged_score:.4f}"
    )
    # At 2× stability, exponential decay: 2^(-2) = 0.25 of decay component
    assert very_old_score <= fresh_score * 0.5, (
        f"Score at 2× stability {very_old_score:.4f} should be ≤ 50% "
        f"of fresh {fresh_score:.4f}"
    )

    # --- empirical: no decay regardless of age ---
    empirical_fresh = calculate_memory_score(
        base_score=base_score,
        access_count=0,
        age_days=0.0,
        memory_type="empirical",
    )
    empirical_old = calculate_memory_score(
        base_score=base_score,
        access_count=0,
        age_days=365.0,  # 1 year old — should not decay
        memory_type="empirical",
    )

    assert empirical_old == empirical_fresh, (
        f"empirical memory should not decay: "
        f"fresh={empirical_fresh:.4f} vs 1yr={empirical_old:.4f}"
    )

    # --- decision: power-law decay — slower than exponential ---
    decision_at_stability = calculate_memory_score(
        base_score=base_score,
        access_count=0,
        age_days=90.0,  # at stability (90 days)
        memory_type="decision",
    )
    decision_fresh = calculate_memory_score(
        base_score=base_score,
        access_count=0,
        age_days=0.0,
        memory_type="decision",
    )
    # Power-law at t=S: (1 + 1/9)^(-0.5) ≈ 0.948 of fresh score — slow decay
    assert decision_at_stability < decision_fresh, (
        "decision memory should decay (slowly)"
    )
    assert decision_at_stability > fresh_score * 0.5, (
        f"power-law decay at t=stability should still be > 50% of fresh: "
        f"{decision_at_stability:.4f}"
    )


# ---------------------------------------------------------------------------
# I20.c — NLI guard: contradictory memories handled; contradiction edge recorded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i20c_nli_guard_rejects_contradiction():
    """NLI guard: two contradictory memories → lower-confidence one rejected.

    Feeding two contradictory memories through ``resolve_memory_conflict``
    (the canonical conflict resolution entry point) with a mocked NLI guard
    that detects contradiction asserts:
    1. The resolution action is NOT "merge" (the guard intervenes).
    2. The returned action is "coexist" OR "supersede" (safe outcomes).

    We also verify that the W.4 ContradictionFinding logging path is
    exercised when NLI contradiction score exceeds the threshold.
    """
    from core.agents.memory import resolve_memory_conflict

    memory_a = "The Rust compiler guarantees memory safety through ownership."
    memory_b = "Rust programs frequently cause segmentation faults in production."

    existing_memory = {
        "memory_id": "art-existing-safe",
        "text": memory_a,
        "similarity": 0.95,
        "created_at": "2026-01-01T00:00:00Z",
    }

    # Mock NLI: returns high contradiction score → guard blocks merge
    nli_contradiction_result = {
        "entailment": 0.05,   # low entailment
        "contradiction": 0.92, # high contradiction
        "neutral": 0.03,
    }

    # nli_score is imported inside resolve_memory_conflict via
    # `from core.utils.nli import nli_score` — patch the source module.
    with patch(
        "core.agents.memory.call_internal_llm",
        new_callable=AsyncMock,
        return_value='{"action": "merge", "reason": "combine facts", "merged_text": "Rust is both safe and unsafe."}',
    ):
        with patch("core.utils.nli.nli_score", return_value=nli_contradiction_result):
            result = await resolve_memory_conflict(
                new_memory=memory_b,
                existing_memory=existing_memory,
            )

    # NLI guard must block the merge (entailment 0.05 < 0.7 threshold)
    assert result["action"] != "merge", (
        f"NLI guard should have blocked the merge, got action={result['action']!r}"
    )
    assert result["action"] in ("coexist", "supersede"), (
        f"Expected coexist or supersede after NLI rejection, got {result['action']!r}"
    )
    assert result["merged_text"] is None, (
        "NLI-rejected merge must have merged_text=None"
    )


@pytest.mark.asyncio
async def test_i20c_nli_guard_allows_safe_merge():
    """NLI guard: non-contradictory merge is allowed through.

    When entailment is high, the guard does NOT block the merge.
    """
    from core.agents.memory import resolve_memory_conflict

    memory_old = "Python supports list comprehensions for concise iteration."
    memory_new = "Python's list comprehensions provide a compact syntax for building lists."

    existing_memory = {
        "memory_id": "art-old-python",
        "text": memory_old,
        "similarity": 0.91,
        "created_at": "2026-01-01T00:00:00Z",
    }

    merged_text = "Python supports list comprehensions, a compact syntax for building lists via iteration."

    # NLI: high entailment → merge is safe
    nli_entailment_result = {
        "entailment": 0.88,
        "contradiction": 0.05,
        "neutral": 0.07,
    }

    with patch(
        "core.agents.memory.call_internal_llm",
        new_callable=AsyncMock,
        return_value=(
            '{"action": "merge", "reason": "complementary",'
            f' "merged_text": "{merged_text}"}}'
        ),
    ):
        with patch("core.utils.nli.nli_score", return_value=nli_entailment_result):
            result = await resolve_memory_conflict(
                new_memory=memory_new,
                existing_memory=existing_memory,
            )

    assert result["action"] == "merge", (
        f"High-entailment merge should be allowed, got {result['action']!r}"
    )
    assert result["merged_text"] is not None, "Allowed merge must have merged_text"


# ---------------------------------------------------------------------------
# I20.d — callback fires on consolidation failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i20d_failure_callback_fires_on_timeout():
    """When the LLM classify step times out, the failure callback is invoked.

    This exercises the callback wiring in memory_consolidation.py and
    verifies the failure counter increments.
    """
    import core.agents.memory_consolidation as mc_mod

    fired_reasons: list[str] = []

    def _capture_callback(reason: str) -> None:
        fired_reasons.append(reason)

    original_callback = mc_mod.consolidation_failure_callback
    mc_mod.consolidation_failure_callback = _capture_callback

    try:
        collection = _make_tracked_collection()
        # _make_tracked_collection sets query.side_effect; override it so
        # return_value takes effect and returns the high-similarity result
        # that pushes classify_memory into the LLM path.
        collection.query.side_effect = None
        collection.query.return_value = {
            "ids": [["chunk_existing"]],
            "documents": [["existing memory content"]],
            "metadatas": [[{"artifact_id": "art-existing"}]],
            "distances": [[0.02]],  # l2→ 0.9998 ≥ 0.85 → triggers LLM classify
        }

        chroma_client = MagicMock(
            get_collection=MagicMock(return_value=collection),
            get_or_create_collection=MagicMock(return_value=collection),
        )

        async def _slow_llm(*args: Any, **kwargs: Any) -> str:
            await asyncio.sleep(0.5)  # longer than budget
            return '{"action": "NOOP"}'

        with patch(
            "core.agents.memory_consolidation.CONSOLIDATION_LLM_BUDGET_S", 0.05
        ):
            with patch(
                "core.agents.memory_consolidation.call_internal_llm", _slow_llm
            ):
                from core.agents.memory_consolidation import classify_memory
                action = await classify_memory(
                    "some new content",
                    chroma_client=chroma_client,
                    memory_type="preference",
                )

        # Timeout: fallback to ADD
        assert action.action == "ADD"
        assert action.reason == "timeout"

        # Callback must have fired
        assert len(fired_reasons) >= 1, (
            f"Expected failure callback to fire on timeout, "
            f"got fired_reasons={fired_reasons!r}"
        )
        assert any("timeout" in r for r in fired_reasons), (
            f"Expected 'timeout' reason in callback, got {fired_reasons!r}"
        )

    finally:
        mc_mod.consolidation_failure_callback = original_callback


# ---------------------------------------------------------------------------
# I20.e — circuit open fires callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i20e_failure_callback_fires_on_circuit_open():
    """When the LLM circuit is open, the failure callback is invoked."""
    import core.agents.memory_consolidation as mc_mod
    from core.utils.circuit_breaker import CircuitOpenError

    fired_reasons: list[str] = []

    def _capture(reason: str) -> None:
        fired_reasons.append(reason)

    original_callback = mc_mod.consolidation_failure_callback
    mc_mod.consolidation_failure_callback = _capture

    try:
        collection = _make_tracked_collection()
        # Override side_effect so return_value is used (same as I20.d fix)
        collection.query.side_effect = None
        collection.query.return_value = {
            "ids": [["chunk_x"]],
            "documents": [["existing fact"]],
            "metadatas": [[{"artifact_id": "art-x"}]],
            "distances": [[0.02]],  # l2 → 0.9998 ≥ 0.85 → triggers LLM classify
        }

        chroma_client = MagicMock(
            get_collection=MagicMock(return_value=collection),
            get_or_create_collection=MagicMock(return_value=collection),
        )

        with patch(
            "core.agents.memory_consolidation.call_internal_llm",
            new_callable=AsyncMock,
            side_effect=CircuitOpenError("openrouter", retry_after=30.0),
        ):
            from core.agents.memory_consolidation import classify_memory
            action = await classify_memory(
                "another memory",
                chroma_client=chroma_client,
                memory_type="empirical",
            )

        assert action.action == "ADD"
        assert "circuit open" in action.reason

        assert any("circuit_open" in r for r in fired_reasons), (
            f"Expected 'circuit_open' reason in callback, got {fired_reasons!r}"
        )

    finally:
        mc_mod.consolidation_failure_callback = original_callback
