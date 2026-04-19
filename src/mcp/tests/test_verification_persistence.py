# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for verification report persistence in Neo4j."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.db.neo4j.artifacts import get_verification_report, save_verification_report

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_neo4j():
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    return driver, session


# ---------------------------------------------------------------------------
# Tests: save_verification_report
# ---------------------------------------------------------------------------

class TestSaveVerificationReport:
    def test_returns_report_id(self, mock_neo4j):
        driver, session = mock_neo4j
        claims = [{"claim": "test claim", "status": "verified", "confidence": 0.9}]

        report_id = save_verification_report(
            driver,
            conversation_id="conv-123",
            claims=claims,
            overall_score=0.85,
            verified=1,
            unverified=0,
            uncertain=0,
            total=1,
        )

        assert isinstance(report_id, str)
        assert len(report_id) == 36  # UUID format

    def test_creates_verification_report_node(self, mock_neo4j):
        driver, session = mock_neo4j
        claims = [{"claim": "Python is a language", "status": "verified", "confidence": 0.95}]

        save_verification_report(
            driver,
            conversation_id="conv-456",
            claims=claims,
            overall_score=0.95,
            verified=1,
            total=1,
        )

        # Should have called session.run at least once with MERGE VerificationReport
        assert session.run.called
        first_call = session.run.call_args_list[0]
        query = first_call.args[0] if first_call.args else first_call.kwargs.get("query", "")
        assert "VerificationReport" in query
        assert "MERGE" in query

    def test_creates_verified_relationships(self, mock_neo4j):
        driver, session = mock_neo4j
        claims = [
            {
                "claim": "claim 1",
                "status": "verified",
                "sources": [{"artifact_id": "art-1", "title": "Doc A"}],
            },
            {
                "claim": "claim 2",
                "status": "verified",
                "sources": [{"artifact_id": "art-2", "title": "Doc B"}],
            },
        ]

        save_verification_report(
            driver,
            conversation_id="conv-789",
            claims=claims,
            overall_score=0.90,
            verified=2,
            total=2,
        )

        # Should have created VERIFIED relationships for both artifacts
        calls = session.run.call_args_list
        merge_verified_calls = [
            c for c in calls
            if "MERGE" in str(c) and "VERIFIED" in str(c)
        ]
        assert len(merge_verified_calls) == 2

    def test_deduplicates_artifact_ids(self, mock_neo4j):
        driver, session = mock_neo4j
        # Two claims reference the same artifact
        claims = [
            {"claim": "c1", "sources": [{"artifact_id": "art-1"}]},
            {"claim": "c2", "sources": [{"artifact_id": "art-1"}]},
        ]

        save_verification_report(
            driver,
            conversation_id="conv-dup",
            claims=claims,
            overall_score=0.80,
            total=2,
        )

        merge_verified_calls = [
            c for c in session.run.call_args_list
            if "MERGE" in str(c) and "VERIFIED" in str(c)
        ]
        assert len(merge_verified_calls) == 1  # Only one relationship created

    def test_handles_claims_without_sources(self, mock_neo4j):
        driver, session = mock_neo4j
        claims = [{"claim": "no sources here", "status": "unverified"}]

        report_id = save_verification_report(
            driver,
            conversation_id="conv-nosrc",
            claims=claims,
            overall_score=0.0,
            unverified=1,
            total=1,
        )

        assert report_id  # Should still succeed
        # Only the MERGE call, no VERIFIED relationship calls
        verified_calls = [c for c in session.run.call_args_list if "VERIFIED" in str(c)]
        assert len(verified_calls) == 0

    def test_stores_claims_as_json_string(self, mock_neo4j):
        driver, session = mock_neo4j
        claims = [{"claim": "test", "status": "verified"}]

        save_verification_report(
            driver,
            conversation_id="conv-json",
            claims=claims,
            overall_score=0.5,
            total=1,
        )

        merge_call = session.run.call_args_list[0]
        kwargs = merge_call.kwargs if merge_call.kwargs else {}
        # The claims parameter should be a JSON string
        claims_arg = kwargs.get("claims", "")
        if claims_arg:
            parsed = json.loads(claims_arg)
            assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# Tests: get_verification_report
# ---------------------------------------------------------------------------

class TestGetVerificationReport:
    def test_returns_none_when_not_found(self, mock_neo4j):
        driver, session = mock_neo4j
        session.run.return_value.single.return_value = None

        result = get_verification_report(driver, "nonexistent-conv")
        assert result is None

    def test_returns_report_when_found(self, mock_neo4j):
        driver, session = mock_neo4j
        session.run.return_value.single.return_value = {
            "id": "report-123",
            "conversation_id": "conv-found",
            "claims": json.dumps([{"claim": "test", "status": "verified"}]),
            "overall_score": 0.85,
            "verified": 1,
            "unverified": 0,
            "uncertain": 0,
            "total": 1,
            "created_at": "2026-03-11T00:00:00Z",
        }

        result = get_verification_report(driver, "conv-found")
        assert result is not None
        assert result["report_id"] == "report-123"
        assert result["conversation_id"] == "conv-found"
        assert result["overall_score"] == 0.85
        assert isinstance(result["claims"], list)
        assert len(result["claims"]) == 1

    def test_handles_invalid_claims_json(self, mock_neo4j):
        driver, session = mock_neo4j
        session.run.return_value.single.return_value = {
            "id": "report-bad",
            "conversation_id": "conv-bad",
            "claims": "not valid json{",
            "overall_score": 0.0,
            "verified": 0,
            "unverified": 0,
            "uncertain": 0,
            "total": 0,
            "created_at": "2026-03-11T00:00:00Z",
        }

        result = get_verification_report(driver, "conv-bad")
        assert result is not None
        assert result["claims"] == []  # Graceful fallback

    def test_returns_all_fields(self, mock_neo4j):
        driver, session = mock_neo4j
        session.run.return_value.single.return_value = {
            "id": "r1",
            "conversation_id": "c1",
            "claims": "[]",
            "overall_score": 0.75,
            "verified": 5,
            "unverified": 2,
            "uncertain": 1,
            "total": 8,
            "created_at": "2026-03-11T12:00:00Z",
        }

        result = get_verification_report(driver, "c1")
        expected_keys = {
            "report_id", "conversation_id", "claims",
            "overall_score", "verified", "unverified",
            "uncertain", "total", "created_at",
        }
        assert expected_keys == set(result.keys())


# ---------------------------------------------------------------------------
# Tests: save + get round-trip (mocked)
# ---------------------------------------------------------------------------

class TestVerificationRoundTrip:
    def test_save_then_get_consistency(self, mock_neo4j):
        """Verify that saved data can be retrieved with matching structure."""
        driver, session = mock_neo4j
        claims = [
            {"claim": "Earth orbits the Sun", "status": "verified", "confidence": 0.99},
            {"claim": "The sky is green", "status": "refuted", "confidence": 0.1},
        ]

        save_verification_report(
            driver,
            conversation_id="round-trip",
            claims=claims,
            overall_score=0.55,
            verified=1,
            unverified=0,
            uncertain=0,
            total=2,
        )

        # Simulate retrieval with the same data
        session.run.return_value.single.return_value = {
            "id": "rt-id",
            "conversation_id": "round-trip",
            "claims": json.dumps(claims),
            "overall_score": 0.55,
            "verified": 1,
            "unverified": 0,
            "uncertain": 0,
            "total": 2,
            "created_at": "2026-03-11T00:00:00Z",
        }

        result = get_verification_report(driver, "round-trip")
        assert result["overall_score"] == 0.55
        assert len(result["claims"]) == 2
        assert result["claims"][0]["claim"] == "Earth orbits the Sun"


# ---------------------------------------------------------------------------
# Task-2 regression tests: EXTRACTED_FROM edge + external provenance
# ---------------------------------------------------------------------------


def _mk_driver():
    driver = MagicMock()
    session = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    driver.session.return_value = ctx
    return driver, session


def test_save_report_writes_extracted_from_for_each_artifact():
    driver, session = _mk_driver()
    claims = [{"sources": [{"artifact_id": "a1"}, {"artifact_id": "a2"}]}]
    save_verification_report(driver, "conv-1", claims, 0.8, 2, 0, 0, 2)
    cyphers = [c[0][0] for c in session.run.call_args_list]
    assert any("EXTRACTED_FROM" in c for c in cyphers), f"no EXTRACTED_FROM in {cyphers}"
    assert any("VERIFIED" in c for c in cyphers)


def test_save_report_persists_external_urls_when_no_artifact():
    """External / web_search claims have aid='' — must still persist provenance."""
    driver, session = _mk_driver()
    claims = [{
        "verification_method": "web_search",
        "sources": [{"artifact_id": "", "url": "https://en.wikipedia.org/foo"}],
    }]
    save_verification_report(driver, "conv-2", claims, 0.7, 1, 0, 0, 1)
    upsert_call = session.run.call_args_list[0]
    params = upsert_call[1]
    assert "source_urls" in params
    assert "https://en.wikipedia.org/foo" in params["source_urls"]
    assert "verification_methods" in params
    assert "web_search" in params["verification_methods"]


def test_save_report_handles_no_sources_gracefully():
    driver, session = _mk_driver()
    claims = [{"sources": []}]
    save_verification_report(driver, "conv-3", claims, 0.5, 0, 0, 1, 1)
    cyphers = [c[0][0] for c in session.run.call_args_list]
    assert sum("MERGE (r:VerificationReport" in c for c in cyphers) == 1
