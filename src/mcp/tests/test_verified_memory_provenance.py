# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""``promote_verified_facts`` against the shape production actually emits.

Two defects found by the 2026-07-29 GA audit, both invisible to the existing
suite because ``test_pipeline_enhancements.py`` feeds fixtures shaped like the
*reader* rather than like the *emitter*:

1. **Provenance was always empty.** The promoter read the speculative nested
   ``sources: [{artifact_id}]`` shape; ``verify_claim`` has only ever emitted
   the flat ``source_artifact_id``. Every empirical memory ever promoted got
   ``artifact_id=""`` and could not be traced to its source artifact.

2. **The meta-claim filter was dead.** It read ``claim_data["type"]``, a key
   nothing in the live pipeline sets — ``streaming._claim_type`` computed the
   category but merged it only into the SSE event, never back into the claims
   array that becomes the promotion input. So ``"I don't have data on that"``
   was eligible for promotion to a *permanent, non-decaying* empirical memory.

Fixtures here are copied from the real emitter's shape, not the reader's.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    return asyncio.run(coro)


def _promote(claims, create_fn):
    from core.agents.verified_memory import promote_verified_facts

    neo4j = MagicMock()
    neo4j.session.return_value.__enter__ = MagicMock(return_value=MagicMock())
    neo4j.session.return_value.__exit__ = MagicMock(return_value=False)
    chroma = MagicMock()
    chroma.get_or_create_collection.return_value = MagicMock()

    with patch(
        "core.agents.memory.detect_memory_conflict",
        new_callable=AsyncMock,
        return_value=[],
    ):
        return _run(promote_verified_facts(
            {"conversation_id": "conv-prod", "claims": claims},
            chroma, neo4j, create_memory_fn=create_fn,
        ))


# The shape verify_claim really returns (flat provenance, "status" not
# "verdict", "similarity" not "confidence", "claim_type" not "type").
def _production_claim(**overrides):
    claim = {
        "claim": "The Eiffel Tower is in Paris",
        "status": "verified",
        "similarity": 0.94,
        "confidence": 0.94,
        "claim_type": "factual",
        "nli_entailment": 0.91,
        "source_artifact_id": "artifact-abc123",
        "source_filename": "paris.md",
        "verification_method": "kb_nli",
    }
    claim.update(overrides)
    return claim


def test_flat_provenance_reaches_the_promoted_memory():
    """The production shape must produce a traceable memory."""
    create = MagicMock(return_value="mem-1")
    counts = _promote([_production_claim()], create)

    assert counts["promoted"] == 1, counts
    written = create.call_args[0][1]
    assert written["artifact_id"] == "artifact-abc123", (
        "promoted memory lost its source artifact — provenance is unrecoverable "
        f"and the :Memory node is orphaned from its evidence: {written}"
    )


def test_nested_sources_provenance_still_resolves():
    """The speculative nested shape must keep working via the canonical adapter."""
    create = MagicMock(return_value="mem-2")
    claim = _production_claim(source_artifact_id="")
    claim["sources"] = [{"artifact_id": "artifact-nested", "filename": "n.md"}]

    counts = _promote([claim], create)

    assert counts["promoted"] == 1, counts
    assert create.call_args[0][1]["artifact_id"] == "artifact-nested"


@pytest.mark.parametrize("meta_type", ["ignorance", "evasion", "citation"])
def test_meta_claims_are_skipped_on_the_production_key(meta_type):
    """claim_type (live key) must gate promotion, not just legacy "type"."""
    create = MagicMock(return_value="mem-3")
    counts = _promote(
        [_production_claim(
            claim="I don't have information about that",
            claim_type=meta_type,
        )],
        create,
    )

    assert counts["skipped_type"] == 1, (
        f"a {meta_type!r} meta-claim was not filtered: {counts}. It would become "
        "a permanent empirical memory with the highest retrieval authority."
    )
    assert counts["promoted"] == 0
    create.assert_not_called()


def test_legacy_type_key_still_honoured():
    """Older persisted reports use "type" — do not regress them."""
    create = MagicMock(return_value="mem-4")
    claim = _production_claim(claim_type=None)
    claim.pop("claim_type")
    claim["type"] = "ignorance"

    counts = _promote([claim], create)
    assert counts["skipped_type"] == 1, counts


def test_streaming_stamps_claim_type_into_the_promotion_input():
    """The emitter must write the category back, not just into the SSE event.

    Without this the filter above is unreachable in production regardless of
    which key it reads.
    """
    src = __import__("pathlib").Path(
        __file__
    ).resolve().parents[1] / "core" / "agents" / "hallucination" / "streaming.py"
    body = src.read_text(encoding="utf-8")
    anchor = body.split("run_claims = [r for r in collected_results", 1)
    assert len(anchor) == 2, "run_claims construction not found"
    following = anchor[1][:800]
    assert "_claim_type(" in following and "claim_type" in following, (
        "run_claims is built without stamping claim_type; promote_verified_facts "
        "will receive untyped claims and its meta-claim filter goes dead again."
    )
