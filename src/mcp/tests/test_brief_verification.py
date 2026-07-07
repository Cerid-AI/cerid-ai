# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the Task 2.1b generation-time claim-verification pass.

Covers three layers, kept independent so a failure pinpoints the layer:
* ``status_to_band`` — the pure verdict -> trust-band mapping table.
* ``verify_brief_claims`` — extraction + verification orchestration,
  with ``core.agents.hallucination.extract_claims`` / ``verify_claims``
  mocked (no real LLM / KB calls).
* ``save_verified_claims`` — the Neo4j write path, against the shared
  ``mock_neo4j`` fixture (driver/session mock from conftest.py).

Job-level best-effort wiring (a verification failure must not fail brief
generation) is covered separately in ``test_brief_generation_job.py`` /
``test_weekly_synthesis_job.py``.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# status_to_band — pure mapping table
# ---------------------------------------------------------------------------


class TestStatusToBand:
    @pytest.mark.parametrize(
        "status,expected_band",
        [
            ("verified", "verified"),
            ("uncertain", "partial"),
            ("unverified", "unverified"),
            ("error", "unverified"),
            ("skipped", "unverified"),  # unmapped status must never over-claim
            ("", "unverified"),
        ],
    )
    def test_band_mapping(self, status, expected_band):
        from app.services.briefs.verification import status_to_band

        assert status_to_band(status) == expected_band


# ---------------------------------------------------------------------------
# verify_brief_claims
# ---------------------------------------------------------------------------


class TestVerifyBriefClaims:
    async def test_returns_banded_claims_for_mixed_verdicts(self):
        from app.services.briefs import verification as verif_mod

        sections = {
            "CONNECTIONS": "Fact one is true.",
            "PATTERN": "Fact two is unclear.",
        }

        with (
            patch(
                "core.agents.hallucination.extract_claims",
                new=AsyncMock(
                    return_value=(
                        ["Fact one is true.", "Fact two is unclear."],
                        "llm",
                    )
                ),
            ),
            patch(
                "core.agents.hallucination.verify_claims",
                new=AsyncMock(
                    return_value=[
                        {"status": "verified", "source_artifact_id": "art-1"},
                        {"status": "uncertain"},
                    ]
                ),
            ),
        ):
            claims = await verif_mod.verify_brief_claims(
                sections, chroma_client=MagicMock()
            )

        assert len(claims) == 2

        assert claims[0]["text"] == "Fact one is true."
        assert claims[0]["band"] == "verified"
        assert claims[0]["source_ids"] == ["art-1"]

        assert claims[1]["text"] == "Fact two is unclear."
        assert claims[1]["band"] == "partial"
        assert claims[1]["source_ids"] == []

        # claim_ids are freshly minted, distinct uuid4 strings.
        for c in claims:
            uuid.UUID(c["claim_id"])
        assert claims[0]["claim_id"] != claims[1]["claim_id"]

    async def test_empty_sections_short_circuits_before_extraction(self):
        from app.services.briefs import verification as verif_mod

        with patch("core.agents.hallucination.extract_claims") as mock_extract:
            claims = await verif_mod.verify_brief_claims(
                {"CONNECTIONS": "", "PATTERN": "   "}, chroma_client=MagicMock()
            )

        assert claims == []
        mock_extract.assert_not_called()

    async def test_no_claims_extracted_returns_empty_list(self):
        from app.services.briefs import verification as verif_mod

        with (
            patch(
                "core.agents.hallucination.extract_claims",
                new=AsyncMock(return_value=([], "none")),
            ),
            patch("core.agents.hallucination.verify_claims") as mock_verify,
        ):
            claims = await verif_mod.verify_brief_claims(
                {"CONNECTIONS": "some brief text"}, chroma_client=MagicMock()
            )

        assert claims == []
        mock_verify.assert_not_called()

    @pytest.mark.parametrize("method", ["ignorance", "evasion"])
    async def test_ignorance_and_evasion_methods_return_empty(self, method):
        """Even if claims surface, an ignorance/evasion response isn't a
        set of factual claims worth persisting a trust band for."""
        from app.services.briefs import verification as verif_mod

        with (
            patch(
                "core.agents.hallucination.extract_claims",
                new=AsyncMock(return_value=(["I don't have data on X."], method)),
            ),
            patch("core.agents.hallucination.verify_claims") as mock_verify,
        ):
            claims = await verif_mod.verify_brief_claims(
                {"CONNECTIONS": "some brief text"}, chroma_client=MagicMock()
            )

        assert claims == []
        mock_verify.assert_not_called()

    async def test_missing_source_artifact_id_yields_empty_source_ids(self):
        from app.services.briefs import verification as verif_mod

        with (
            patch(
                "core.agents.hallucination.extract_claims",
                new=AsyncMock(return_value=(["Fact one."], "llm")),
            ),
            patch(
                "core.agents.hallucination.verify_claims",
                new=AsyncMock(return_value=[{"status": "unverified"}]),
            ),
        ):
            claims = await verif_mod.verify_brief_claims(
                {"CONNECTIONS": "Fact one."}, chroma_client=MagicMock()
            )

        assert len(claims) == 1
        assert claims[0]["band"] == "unverified"
        assert claims[0]["source_ids"] == []

    async def test_passes_chroma_neo4j_redis_through_to_verify_claims(self):
        from app.services.briefs import verification as verif_mod

        chroma = MagicMock(name="chroma")
        neo4j = MagicMock(name="neo4j")
        redis_client = MagicMock(name="redis")

        with (
            patch(
                "core.agents.hallucination.extract_claims",
                new=AsyncMock(return_value=(["Fact one."], "llm")),
            ),
            patch(
                "core.agents.hallucination.verify_claims",
                new=AsyncMock(return_value=[{"status": "verified"}]),
            ) as mock_verify,
        ):
            await verif_mod.verify_brief_claims(
                {"CONNECTIONS": "Fact one."},
                chroma_client=chroma,
                neo4j_driver=neo4j,
                redis_client=redis_client,
            )

        mock_verify.assert_awaited_once()
        args, kwargs = mock_verify.call_args
        assert args[1] is chroma
        assert kwargs["neo4j_driver"] is neo4j
        assert kwargs["redis_client"] is redis_client


# ---------------------------------------------------------------------------
# save_verified_claims — Neo4j write path
# ---------------------------------------------------------------------------


class TestSaveVerifiedClaims:
    def test_empty_list_is_noop(self, mock_neo4j):
        from app.db.neo4j.briefs import save_verified_claims

        driver, session = mock_neo4j
        save_verified_claims(driver, [])

        session.run.assert_not_called()

    def test_writes_text_band_and_source_edge(self, mock_neo4j):
        from app.db.neo4j.briefs import save_verified_claims

        driver, session = mock_neo4j
        claims = [
            {
                "claim_id": "claim-1",
                "text": "Fact one is true.",
                "band": "verified",
                "source_ids": ["artifact-1"],
            }
        ]

        save_verified_claims(driver, claims)

        assert session.run.call_count == 2

        claim_call = session.run.call_args_list[0]
        cypher = claim_call.args[0]
        assert "MERGE (c:Claim {claim_id: $claim_id})" in cypher
        assert "SET" in cypher
        assert claim_call.kwargs == {
            "claim_id": "claim-1",
            "text": "Fact one is true.",
            "band": "verified",
            "updated_at": claim_call.kwargs["updated_at"],
        }
        assert claim_call.kwargs["updated_at"]  # non-empty timestamp string

        edge_call = session.run.call_args_list[1]
        edge_cypher = edge_call.args[0]
        assert "MATCH (a:Artifact {id: $source_id})" in edge_cypher
        assert "MERGE (c)-[:EXTRACTED_FROM]->(a)" in edge_cypher
        assert edge_call.kwargs == {
            "claim_id": "claim-1",
            "source_id": "artifact-1",
        }

    def test_no_source_ids_skips_artifact_edge(self, mock_neo4j):
        from app.db.neo4j.briefs import save_verified_claims

        driver, session = mock_neo4j
        claims = [
            {
                "claim_id": "claim-2",
                "text": "Fact two.",
                "band": "unverified",
                "source_ids": [],
            }
        ]

        save_verified_claims(driver, claims)

        assert session.run.call_count == 1

    def test_multiple_source_ids_each_get_an_edge(self, mock_neo4j):
        from app.db.neo4j.briefs import save_verified_claims

        driver, session = mock_neo4j
        claims = [
            {
                "claim_id": "claim-3",
                "text": "Fact three.",
                "band": "partial",
                "source_ids": ["artifact-a", "artifact-b"],
            }
        ]

        save_verified_claims(driver, claims)

        # 1 claim MERGE + 2 artifact edges
        assert session.run.call_count == 3

    def test_multiple_claims_each_persisted(self, mock_neo4j):
        from app.db.neo4j.briefs import save_verified_claims

        driver, session = mock_neo4j
        claims = [
            {"claim_id": "c1", "text": "One.", "band": "verified", "source_ids": []},
            {"claim_id": "c2", "text": "Two.", "band": "partial", "source_ids": []},
        ]

        save_verified_claims(driver, claims)

        assert session.run.call_count == 2
        claim_ids_written = {
            call.kwargs["claim_id"] for call in session.run.call_args_list
        }
        assert claim_ids_written == {"c1", "c2"}
