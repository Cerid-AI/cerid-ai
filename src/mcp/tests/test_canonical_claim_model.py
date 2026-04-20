# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contract tests for ``ClaimVerification`` (Sprint B).

These tests codify the adapter contract so the class of drift that
produced the P1.4 bug — flat vs nested vs legacy claim shapes
handled differently in the writer vs the migration — cannot recur.

Three historical shapes are covered:

  1. **Flat singular** (production since v0.84): ``source_artifact_id``,
     ``source_urls``, top-level verification fields.
  2. **Nested sources** (speculative): ``sources: [{artifact_id,
     url, ...}]`` — unread pre-Sprint B, silently dropped.
  3. **Pre-v0.84 legacy**: only ``source_filename`` + ``source_snippet``
     populated; no artifact id at all.

For each, ``from_legacy_dict`` MUST produce a ``ClaimVerification``
that ``.has_provenance()`` + ``.artifact_ids()`` answer consistently,
regardless of input shape. A new shape showing up (shape #4) should
be a one-line change in ``from_legacy_dict``, not a bug hunt across
the writer + two migrations + frontend parser.
"""
from __future__ import annotations

import pytest


def test_flat_singular_shape_round_trips():
    """v0.84 production shape — the one P1.4 exposed."""
    from core.agents.hallucination.models import ClaimVerification

    raw = {
        "claim": "The speed of light is 299,792 km/s",
        "status": "verified",
        "similarity": 0.98,
        "source_artifact_id": "abc-123",
        "source_filename": "physics.pdf",
        "source_domain": "general",
        "source_snippet": "speed of light in a vacuum...",
        "verification_method": "kb_nli",
        "nli_entailment": 0.985,
    }
    c = ClaimVerification.from_legacy_dict(raw)
    assert c.source_artifact_id == "abc-123"
    assert c.artifact_ids() == ["abc-123"]
    assert c.has_provenance()
    assert c.verification_method == "kb_nli"


def test_nested_sources_shape_normalized_to_flat():
    """Speculative shape — first nested artifact_id wins the flat slot."""
    from core.agents.hallucination.models import ClaimVerification

    raw = {
        "claim": "Paris is the capital of France",
        "status": "verified",
        "sources": [
            {"artifact_id": "art-1", "domain": "general", "filename": "geo.md"},
            {"artifact_id": "art-2", "domain": "general", "filename": "geo2.md"},
        ],
    }
    c = ClaimVerification.from_legacy_dict(raw)
    assert c.source_artifact_id == "art-1"
    assert c.artifact_ids() == ["art-1"]
    assert c.source_filename == "geo.md"
    assert c.has_provenance()


def test_nested_urls_merge_into_flat_list():
    from core.agents.hallucination.models import ClaimVerification

    raw = {
        "claim": "Eiffel Tower completed 1889",
        "status": "verified",
        "source_urls": ["https://a.example/article"],
        "sources": [
            {"url": "https://b.example/article"},
            {"source_url": "https://c.example/article"},
            {"url": "https://a.example/article"},  # dedup
        ],
    }
    c = ClaimVerification.from_legacy_dict(raw)
    assert set(c.source_urls) == {
        "https://a.example/article",
        "https://b.example/article",
        "https://c.example/article",
    }
    assert c.has_provenance()


def test_legacy_source_alias_maps_to_filename():
    """Older claim dicts used 'source' as the filename key."""
    from core.agents.hallucination.models import ClaimVerification

    raw = {
        "claim": "Water freezes at 0C",
        "status": "verified",
        "source": "chemistry.txt",
        "source_snippet": "...",
    }
    c = ClaimVerification.from_legacy_dict(raw)
    assert c.source_filename == "chemistry.txt"


def test_has_provenance_returns_false_for_stub_claims():
    """Stub claims with no provenance at all — the m0002 cleanup
    predicate must agree with ``has_provenance()``."""
    from core.agents.hallucination.models import ClaimVerification

    raw = {
        "claim": "orphan stub",
        "status": "uncertain",
        "verification_method": "none",
    }
    c = ClaimVerification.from_legacy_dict(raw)
    assert c.has_provenance() is False
    assert c.artifact_ids() == []


def test_round_trip_through_model_dump_preserves_shape():
    """Wire compatibility: model_dump must produce a dict the
    frontend's HallucinationClaim type can accept unchanged."""
    from core.agents.hallucination.models import ClaimVerification

    raw = {
        "claim": "Python was released in 1991",
        "claim_type": "factual",
        "status": "verified",
        "similarity": 0.9,
        "source_artifact_id": "py-1",
        "source_urls": ["https://python.org"],
        "verification_method": "kb_nli",
        "nli_entailment": 0.92,
        "nli_contradiction": 0.01,
    }
    c = ClaimVerification.from_legacy_dict(raw)
    dumped = c.model_dump(mode="json", exclude_none=False)
    # All seven FE-critical fields must survive the round-trip.
    for key in (
        "claim",
        "status",
        "source_artifact_id",
        "source_urls",
        "verification_method",
        "nli_entailment",
        "nli_contradiction",
    ):
        assert key in dumped, f"round-trip dropped {key!r}"
        assert dumped[key] == raw[key], f"round-trip mutated {key!r}"


def test_passthrough_when_input_is_already_canonical():
    """Idempotent: from_legacy_dict on a ClaimVerification returns it."""
    from core.agents.hallucination.models import ClaimVerification

    c1 = ClaimVerification(claim="test", status="verified", source_artifact_id="x")
    c2 = ClaimVerification.from_legacy_dict(c1)
    assert c1 is c2  # same object


def test_from_legacy_dict_rejects_non_dict_inputs():
    from core.agents.hallucination.models import ClaimVerification

    for bad in ("string", 42, None, [1, 2]):
        with pytest.raises(TypeError):
            ClaimVerification.from_legacy_dict(bad)  # type: ignore[arg-type]


def test_claim_verification_list_handles_mixed_shapes():
    """A single persisted claims JSON can mix shapes (legacy + modern)
    if the report was migrated in-place. The container model must
    normalize all entries."""
    from core.agents.hallucination.models import ClaimVerificationList

    raw = [
        {"claim": "a", "status": "verified", "source_artifact_id": "x"},
        {"claim": "b", "status": "verified", "sources": [{"artifact_id": "y"}]},
        # True stub — method=="none" signals "verifier did not run",
        # which is the only condition that leaves a claim with zero
        # provenance. Matches the m0002 orphan predicate semantics.
        {"claim": "c", "status": "uncertain", "verification_method": "none"},
    ]
    batch = ClaimVerificationList.from_legacy(raw)
    assert len(batch.claims) == 3
    assert batch.claims[0].artifact_ids() == ["x"]
    assert batch.claims[1].artifact_ids() == ["y"]
    assert batch.claims[2].has_provenance() is False
