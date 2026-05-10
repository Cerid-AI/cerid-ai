# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the contradiction log service (Phase W.4).

Tests run without a live Neo4j instance — the adapter layer is mocked
at the service boundary. Tests cover:

1. log → list → get round-trip (via service direct-call)
2. entity-slug filtering
3. severity validation
4. pagination (limit respected)
5. NLI-guard simulation: calling the service directly proves the surface
   is ready for integration without coupling to verification.py.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.services.contradiction_log import (
    ContradictionFinding,
    _build_finding_from_row,
    get_by_entity,
    get_by_id,
    list_recent,
    log_contradiction,
    make_finding_id,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_finding(**overrides: Any) -> ContradictionFinding:
    """Build a minimal valid ContradictionFinding."""
    defaults: dict[str, Any] = {
        "finding_id": "test-finding-001",
        "claim_a_id": "claim-a-001",
        "claim_b_id": "claim-b-001",
        "claim_a_text": "The Earth orbits the Sun.",
        "claim_b_text": "The Sun orbits the Earth.",
        "severity": "high",
        "detected_at": "2026-05-10T12:00:00Z",
        "entity_slug": "solar-system",
        "source_artifacts": ["artifact-001", "artifact-002"],
    }
    defaults.update(overrides)
    return ContradictionFinding(**defaults)


def _finding_to_row(f: ContradictionFinding) -> dict[str, Any]:
    """Convert a finding back to a Neo4j-style property dict for mocking."""
    return {
        "finding_id": f.finding_id,
        "claim_a_id": f.claim_a_id,
        "claim_b_id": f.claim_b_id,
        "claim_a_text": f.claim_a_text,
        "claim_b_text": f.claim_b_text,
        "entity_slug": f.entity_slug or "",
        "severity": f.severity,
        "detected_at": f.detected_at,
        "query_ctx_id": f.query_ctx_id or "",
        "source_artifacts": ",".join(f.source_artifacts),
    }


# ---------------------------------------------------------------------------
# ContradictionFinding model
# ---------------------------------------------------------------------------


class TestContradictionFindingModel:
    def test_valid_finding_round_trips(self) -> None:
        f = _make_finding()
        assert f.finding_id == "test-finding-001"
        assert f.severity == "high"
        assert f.source_artifacts == ["artifact-001", "artifact-002"]

    def test_invalid_severity_raises(self) -> None:
        with pytest.raises(ValidationError):
            _make_finding(severity="critical")

    def test_severity_values_all_valid(self) -> None:
        for sev in ("low", "medium", "high"):
            f = _make_finding(severity=sev)
            assert f.severity == sev

    def test_detected_at_autofilled_when_empty(self) -> None:
        f = ContradictionFinding(
            finding_id="x",
            claim_a_id="a",
            claim_b_id="b",
            claim_a_text="A says X",
            claim_b_text="B says not-X",
        )
        assert f.detected_at  # non-empty

    def test_optional_fields_default_none(self) -> None:
        f = _make_finding(entity_slug=None, query_ctx_id=None, source_artifacts=[])
        assert f.entity_slug is None
        assert f.query_ctx_id is None
        assert f.source_artifacts == []

    def test_make_finding_id_returns_hex_string(self) -> None:
        fid = make_finding_id()
        assert len(fid) == 32
        int(fid, 16)  # raises if not hex


# ---------------------------------------------------------------------------
# Row ↔ model conversion
# ---------------------------------------------------------------------------


class TestBuildFindingFromRow:
    def test_round_trip_through_row(self) -> None:
        original = _make_finding()
        row = _finding_to_row(original)
        rebuilt = _build_finding_from_row(row)
        assert rebuilt.finding_id == original.finding_id
        assert rebuilt.claim_a_text == original.claim_a_text
        assert rebuilt.source_artifacts == original.source_artifacts

    def test_empty_source_artifacts_str(self) -> None:
        row = _finding_to_row(_make_finding(source_artifacts=[]))
        rebuilt = _build_finding_from_row(row)
        assert rebuilt.source_artifacts == []

    def test_blank_entity_slug_becomes_none(self) -> None:
        row = _finding_to_row(_make_finding(entity_slug=None))
        rebuilt = _build_finding_from_row(row)
        assert rebuilt.entity_slug is None

    def test_blank_query_ctx_id_becomes_none(self) -> None:
        row = _finding_to_row(_make_finding(query_ctx_id=None))
        rebuilt = _build_finding_from_row(row)
        assert rebuilt.query_ctx_id is None


# ---------------------------------------------------------------------------
# Service: log_contradiction
# ---------------------------------------------------------------------------


class TestLogContradiction:
    @pytest.mark.asyncio
    async def test_log_returns_finding_id(self) -> None:
        finding = _make_finding()
        mock_driver = MagicMock()

        with (
            patch("app.deps.get_neo4j", return_value=mock_driver),
            patch(
                "app.services.contradiction_log._neo4j_adapter.record_contradiction",
                return_value=finding.finding_id,
            ) as mock_record,
        ):
            result = await log_contradiction(finding)

        assert result == finding.finding_id
        mock_record.assert_called_once_with(mock_driver, finding)

    @pytest.mark.asyncio
    async def test_log_propagates_adapter_error(self) -> None:
        finding = _make_finding()
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch(
                "app.services.contradiction_log._neo4j_adapter.record_contradiction",
                side_effect=RuntimeError("neo4j down"),
            ),
        ):
            with pytest.raises(RuntimeError, match="neo4j down"):
                await log_contradiction(finding)


# ---------------------------------------------------------------------------
# Service: list_recent
# ---------------------------------------------------------------------------


class TestListRecent:
    @pytest.mark.asyncio
    async def test_list_returns_findings(self) -> None:
        finding = _make_finding()
        rows = [_finding_to_row(finding)]

        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch(
                "app.services.contradiction_log._neo4j_adapter.list_contradictions",
                return_value=rows,
            ),
        ):
            results = await list_recent()

        assert len(results) == 1
        assert results[0].finding_id == finding.finding_id

    @pytest.mark.asyncio
    async def test_entity_slug_filter_forwarded(self) -> None:
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch(
                "app.services.contradiction_log._neo4j_adapter.list_contradictions",
                return_value=[],
            ) as mock_list,
        ):
            await list_recent(entity_slug="solar-system", since="2026-01-01T00:00:00Z", limit=50)

        assert mock_list.call_count == 1
        _args, _kwargs = mock_list.call_args
        assert _kwargs.get("entity_slug") == "solar-system"
        assert _kwargs.get("since") == "2026-01-01T00:00:00Z"
        assert _kwargs.get("limit") == 50

    @pytest.mark.asyncio
    async def test_pagination_limit_respected(self) -> None:
        """Ensure the limit is forwarded to the adapter (capping is adapter's job)."""
        rows = [_finding_to_row(_make_finding(finding_id=f"f-{i}")) for i in range(5)]

        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch(
                "app.services.contradiction_log._neo4j_adapter.list_contradictions",
                return_value=rows,
            ) as mock_list,
        ):
            results = await list_recent(limit=5)

        assert len(results) == 5
        _, kwargs = mock_list.call_args
        assert kwargs["limit"] == 5

    @pytest.mark.asyncio
    async def test_empty_list_when_no_findings(self) -> None:
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch(
                "app.services.contradiction_log._neo4j_adapter.list_contradictions",
                return_value=[],
            ),
        ):
            results = await list_recent()

        assert results == []


# ---------------------------------------------------------------------------
# Service: get_by_id
# ---------------------------------------------------------------------------


class TestGetById:
    @pytest.mark.asyncio
    async def test_get_existing_finding(self) -> None:
        finding = _make_finding()
        row = _finding_to_row(finding)

        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch(
                "app.services.contradiction_log._neo4j_adapter.get_contradiction",
                return_value=row,
            ),
        ):
            result = await get_by_id(finding.finding_id)

        assert result is not None
        assert result.finding_id == finding.finding_id
        assert result.claim_a_text == finding.claim_a_text

    @pytest.mark.asyncio
    async def test_get_missing_finding_returns_none(self) -> None:
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch(
                "app.services.contradiction_log._neo4j_adapter.get_contradiction",
                return_value=None,
            ),
        ):
            result = await get_by_id("nonexistent-id")

        assert result is None


# ---------------------------------------------------------------------------
# Service: get_by_entity
# ---------------------------------------------------------------------------


class TestGetByEntity:
    @pytest.mark.asyncio
    async def test_delegates_to_list_recent_with_slug(self) -> None:
        finding = _make_finding(entity_slug="solar-system")
        rows = [_finding_to_row(finding)]

        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch(
                "app.services.contradiction_log._neo4j_adapter.list_contradictions",
                return_value=rows,
            ) as mock_list,
        ):
            results = await get_by_entity("solar-system")

        assert len(results) == 1
        _, kwargs = mock_list.call_args
        assert kwargs["entity_slug"] == "solar-system"


# ---------------------------------------------------------------------------
# NLI guard simulation (proves the surface is ready for integration)
#
# This test simulates exactly what core/agents/hallucination/verification.py
# will do when Phase W.4 integration lands. The service is called directly
# (no router, no HTTP layer) with a synthetic finding and the full
# log → list → get round-trip is validated.
# ---------------------------------------------------------------------------


class TestNliGuardSimulation:
    @pytest.mark.asyncio
    async def test_nli_guard_call_round_trip(self) -> None:
        """Simulate the NLI guard detecting a contradiction and persisting it."""
        # Build a finding the way the NLI guard will
        fid = make_finding_id()
        finding = ContradictionFinding(
            finding_id=fid,
            claim_a_id="claim-nli-a",
            claim_b_id="claim-nli-b",
            claim_a_text="The referendum passed with 60% support.",
            claim_b_text="The referendum failed — only 38% voted in favour.",
            entity_slug="uk-referendum-2025",
            severity="high",
            query_ctx_id="ctx-abc123",
            source_artifacts=["doc-001", "doc-002"],
        )

        stored_row = _finding_to_row(finding)

        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch(
                "app.services.contradiction_log._neo4j_adapter.record_contradiction",
                return_value=fid,
            ),
            patch(
                "app.services.contradiction_log._neo4j_adapter.list_contradictions",
                return_value=[stored_row],
            ),
            patch(
                "app.services.contradiction_log._neo4j_adapter.get_contradiction",
                return_value=stored_row,
            ),
        ):
            # Step 1: log (what the NLI guard will do)
            returned_id = await log_contradiction(finding)
            assert returned_id == fid

            # Step 2: list — surfaces in entity query
            results = await list_recent(entity_slug="uk-referendum-2025")
            assert len(results) == 1
            assert results[0].severity == "high"
            assert results[0].entity_slug == "uk-referendum-2025"

            # Step 3: get by ID — direct lookup
            fetched = await get_by_id(fid)
            assert fetched is not None
            assert fetched.claim_a_text == finding.claim_a_text
            assert fetched.source_artifacts == ["doc-001", "doc-002"]
            assert fetched.query_ctx_id == "ctx-abc123"
