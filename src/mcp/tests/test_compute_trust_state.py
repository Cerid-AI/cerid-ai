# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Unit tests for the ComputeTrustStateJob — trust derivation rule."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.processor.jobs.compute_trust_state import (
    _PARTIAL_THRESHOLD,
    _VERIFIED_THRESHOLD,
    ComputeTrustStateJob,
    _count_distribution,
)
from core.processor.priority import Priority

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_driver(rows: list[dict], pack_rows: list[dict] | None = None) -> tuple:
    """Return a mock (driver, session) that yields ``rows`` on the first
    .run().data() call and ``pack_rows`` (default: []) on the second.

    _fetch_trust_scores now issues two queries (verification, then pack),
    so we use side_effect to return distinct data for each call.
    """
    fake_driver = MagicMock()
    fake_session = MagicMock()
    fake_session.__enter__ = lambda self: self
    fake_session.__exit__ = lambda self, exc_type, exc, tb: None
    first_result = MagicMock()
    first_result.data.return_value = rows
    second_result = MagicMock()
    second_result.data.return_value = pack_rows if pack_rows is not None else []
    fake_session.run.side_effect = [first_result, second_result]
    fake_driver.session.return_value = fake_session
    return fake_driver, fake_session


def _noop_cb(_: float) -> None:
    return None


async def _async_noop(_: float) -> None:
    return None


# ---------------------------------------------------------------------------
# Derivation rule unit tests (pure logic — no DB)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verified,total,expected_state", [
    (7, 10, "verified"),   # at threshold
    (9, 10, "verified"),   # above threshold
    (2, 10, "partial"),    # at partial lower bound
    (5, 10, "partial"),    # between thresholds
    (1, 10, "unverified"), # below partial threshold
    (0, 10, "unverified"), # zero verified
    (10, 10, "verified"),  # all verified
])
def test_derivation_thresholds(verified, total, expected_state):
    """Core derivation rule: verify/partial/unverified boundaries are correct."""
    share = verified / total
    if share >= _VERIFIED_THRESHOLD:
        derived = "verified"
    elif share >= _PARTIAL_THRESHOLD:
        derived = "partial"
    else:
        derived = "unverified"
    assert derived == expected_state, (
        f"verified={verified}/{total} ({share:.2f}) expected {expected_state!r} "
        f"but derivation produced {derived!r}"
    )


# ---------------------------------------------------------------------------
# _fetch_trust_scores (DB path)
# ---------------------------------------------------------------------------


def test_fetch_trust_scores_skips_entities_with_zero_evidence():
    """Entities with evidence_total=0 must not appear in results."""
    fake_driver, _ = _make_driver([
        {"entity_id": "e1", "verified_total": 0, "evidence_total": 0},
        {"entity_id": "e2", "verified_total": 5, "evidence_total": 7},
    ])
    job = ComputeTrustStateJob()
    results = job._fetch_trust_scores(fake_driver)
    ids = [r["id"] for r in results]
    assert "e1" not in ids, "Entity with zero evidence must be excluded"
    assert "e2" in ids


def test_fetch_trust_scores_skips_null_entity_id():
    """Rows with null entity_id must be silently skipped."""
    fake_driver, _ = _make_driver([
        {"entity_id": None, "verified_total": 5, "evidence_total": 10},
        {"entity_id": "e1", "verified_total": 8, "evidence_total": 10},
    ])
    job = ComputeTrustStateJob()
    results = job._fetch_trust_scores(fake_driver)
    assert len(results) == 1
    assert results[0]["id"] == "e1"


def test_fetch_trust_scores_assigns_correct_state():
    """End-to-end: fetch returns correct state based on aggregated totals."""
    fake_driver, _ = _make_driver([
        {"entity_id": "high", "verified_total": 8, "evidence_total": 10},
        {"entity_id": "mid",  "verified_total": 3, "evidence_total": 10},
        {"entity_id": "low",  "verified_total": 1, "evidence_total": 10},
    ])
    job = ComputeTrustStateJob()
    results = job._fetch_trust_scores(fake_driver)
    state_map = {r["id"]: r["trust_state"] for r in results}
    assert state_map["high"] == "verified"
    assert state_map["mid"] == "partial"
    assert state_map["low"] == "unverified"


# ---------------------------------------------------------------------------
# _write_trust_states
# ---------------------------------------------------------------------------


def test_write_trust_states_calls_set():
    """Writer must issue a Cypher with SET e.trust_state via UNWIND."""
    fake_driver, fake_session = _make_driver([])
    fake_session.run.return_value = MagicMock()
    job = ComputeTrustStateJob()
    scores = [{"id": f"e{i}", "trust_state": "verified"} for i in range(5)]
    written = job._write_trust_states(fake_driver, scores)
    assert written == 5
    fake_session.run.assert_called_once()
    cypher = fake_session.run.call_args[0][0]
    assert "SET e.trust_state" in cypher
    assert "UNWIND $rows" in cypher


def test_write_trust_states_empty_input_is_noop():
    """Empty scores list must not touch Neo4j."""
    fake_driver, fake_session = _make_driver([])
    job = ComputeTrustStateJob()
    written = job._write_trust_states(fake_driver, [])
    assert written == 0
    fake_session.run.assert_not_called()


def test_write_trust_states_batches_large_input():
    """Large inputs must be split into batches of _WRITE_BATCH."""
    fake_driver, fake_session = _make_driver([])
    # Reset side_effect so unlimited sequential batch calls succeed.
    fake_session.run.side_effect = None
    fake_session.run.return_value = MagicMock()
    job = ComputeTrustStateJob()
    scores = [{"id": f"e{i}", "trust_state": "partial"} for i in range(1100)]
    with patch("app.processor.jobs.compute_trust_state._WRITE_BATCH", 500):
        written = job._write_trust_states(fake_driver, scores)
    assert written == 1100
    assert fake_session.run.call_count == 3


# ---------------------------------------------------------------------------
# _count_distribution
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Pack-trust seeding (Fix 2)
# ---------------------------------------------------------------------------


def test_pack_entity_with_no_verification_evidence_is_seeded_verified():
    """A pack-sourced entity with no VerificationReport evidence → 'verified'."""
    # First query (verification) returns nothing for this entity.
    # Second query (pack) returns one entity.
    fake_driver, _ = _make_driver(
        rows=[],
        pack_rows=[{"entity_id": "pack-only-entity"}],
    )
    job = ComputeTrustStateJob()
    results = job._fetch_trust_scores(fake_driver)
    state_map = {r["id"]: r["trust_state"] for r in results}
    assert state_map.get("pack-only-entity") == "verified", (
        "Pack-sourced entity with no verification evidence must be seeded 'verified'"
    )


def test_pack_entity_with_verification_evidence_keeps_derived_state():
    """An entity covered by VerificationReport keeps its derived state even if
    it is also pack-sourced (verification evidence wins over pack fallback)."""
    # First query returns a partial-verified entity.
    # Second query returns the same entity as pack-sourced.
    fake_driver, _ = _make_driver(
        rows=[{"entity_id": "dual-entity", "verified_total": 3, "evidence_total": 10}],
        pack_rows=[{"entity_id": "dual-entity"}],
    )
    job = ComputeTrustStateJob()
    results = job._fetch_trust_scores(fake_driver)
    state_map = {r["id"]: r["trust_state"] for r in results}
    # 3/10 = 0.30 → 'partial'; pack fallback must NOT override to 'verified'.
    assert state_map.get("dual-entity") == "partial", (
        "Verification-derived trust must win over pack-source fallback"
    )
    # Exactly one result entry for this entity.
    assert sum(1 for r in results if r["id"] == "dual-entity") == 1


def test_pack_query_failure_is_swallowed_and_does_not_break_job():
    """A failure in the pack-trust query must be swallowed; verification results
    still returned."""
    fake_driver = MagicMock()
    fake_session = MagicMock()
    fake_session.__enter__ = lambda self: self
    fake_session.__exit__ = lambda self, exc_type, exc, tb: None

    # First session call (verification) succeeds.
    good_result = MagicMock()
    good_result.data.return_value = [
        {"entity_id": "e1", "verified_total": 8, "evidence_total": 10}
    ]
    # Second session call (pack) raises.
    fake_session.run.side_effect = [good_result, RuntimeError("redis down")]
    fake_driver.session.return_value = fake_session

    job = ComputeTrustStateJob()
    results = job._fetch_trust_scores(fake_driver)
    # Verification result for e1 must still be present.
    state_map = {r["id"]: r["trust_state"] for r in results}
    assert state_map.get("e1") == "verified", (
        "Verification result must survive a pack-query failure"
    )


def test_pack_entity_with_null_id_is_skipped():
    """Pack rows with null entity_id must not appear in results."""
    fake_driver, _ = _make_driver(
        rows=[],
        pack_rows=[{"entity_id": None}, {"entity_id": "good-pack-entity"}],
    )
    job = ComputeTrustStateJob()
    results = job._fetch_trust_scores(fake_driver)
    ids = [r["id"] for r in results]
    assert None not in ids
    assert "good-pack-entity" in ids


def test_count_distribution_tally():
    """_count_distribution must return correct per-state tallies."""
    scores = [
        {"trust_state": "verified"},
        {"trust_state": "verified"},
        {"trust_state": "partial"},
        {"trust_state": "unverified"},
    ]
    dist = _count_distribution(scores)
    assert dist["verified"] == 2
    assert dist["partial"] == 1
    assert dist["unverified"] == 1


# ---------------------------------------------------------------------------
# Job lifecycle
# ---------------------------------------------------------------------------


def test_job_metadata():
    """Job must declare expected properties."""
    job = ComputeTrustStateJob()
    assert job.job_type == "compute_trust_state"
    assert job.priority == Priority.LOW
    cost = job.estimate_cost()
    assert cost.estimated_usd == Decimal("0.00")


def test_run_skips_when_neo4j_unavailable():
    """run() must return skipped metadata when Neo4j is None."""
    job = ComputeTrustStateJob()
    # get_neo4j is imported inside run() via `from app.deps import get_neo4j`
    with patch("app.deps.get_neo4j", return_value=None):
        result = asyncio.run(job.run(_async_noop))
    assert result.metadata["status"] == "skipped"


def test_run_returns_noop_when_no_evidence():
    """run() must return no_op when no VerificationReport covers any entity."""
    job = ComputeTrustStateJob()
    fake_driver = MagicMock()

    with patch("app.deps.get_neo4j", return_value=fake_driver), \
         patch.object(job, "_fetch_trust_scores", return_value=[]):
        result = asyncio.run(job.run(_async_noop))
    assert result.metadata["status"] == "no_op"


def test_run_returns_distribution_on_success():
    """run() must include distribution and written count in metadata."""
    job = ComputeTrustStateJob()
    fake_driver = MagicMock()
    scores = [
        {"id": "e1", "trust_state": "verified"},
        {"id": "e2", "trust_state": "partial"},
        {"id": "e3", "trust_state": "unverified"},
    ]

    with patch("app.deps.get_neo4j", return_value=fake_driver), \
         patch.object(job, "_fetch_trust_scores", return_value=scores), \
         patch.object(job, "_write_trust_states", return_value=3):
        result = asyncio.run(job.run(_async_noop))

    assert result.metadata["written"] == 3
    dist = result.metadata["distribution"]
    assert dist["verified"] == 1
    assert dist["partial"] == 1
    assert dist["unverified"] == 1
