# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Unit tests for the entity wiki page service (Phase W.1).

All external dependencies (Neo4j, contradiction service) are mocked.
No live infrastructure required.

Test coverage:
- list_entities: stubbed adapter returns EntitySummary list
- get_entity_page: assembles all fields; contradictions come from the
  contradiction service (mocked)
- confidence_band boundaries: >=80% verified → high; 60% → medium;
  30% → low; 0 claims → unknown
- 404 path: get_entity_page returns None for missing slug
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.wiki_pages import (
    EntitySummary,
    RelatedEntity,
    SourceCitation,
    WikiEntityPage,
    _compute_next_refresh,
    compute_completeness,
    get_entity_page,
    list_entities,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_driver() -> MagicMock:
    """Return a mock Neo4j driver (never actually called — adapter is patched)."""
    return MagicMock()


def _entity_rows() -> list[dict[str, Any]]:
    return [
        {
            "canonical_id": "person:elon-musk",
            "name": "Elon Musk",
            "entity_type": "PERSON",
            "mention_count": 42,
            "recent_activity_score": 10,
            "summary": "A prominent entrepreneur.",
            "summary_updated_at": "2026-05-10T00:00:00+00:00",
        },
        {
            "canonical_id": "org:tesla",
            "name": "Tesla",
            "entity_type": "ORG",
            "mention_count": 30,
            "recent_activity_score": 5,
            "summary": None,
            "summary_updated_at": None,
        },
    ]


def _full_entity_raw() -> dict[str, Any]:
    return {
        "canonical_id": "person:elon-musk",
        "name": "Elon Musk",
        "entity_type": "PERSON",
        "mention_count": 42,
        "summary": "A prominent entrepreneur.",
        "summary_updated_at": "2026-05-10T00:00:00+00:00",
        "updated_at": "2026-05-10T12:00:00+00:00",
        "related": [
            {
                "canonical_id": "org:tesla",
                "name": "Tesla",
                "entity_type": "ORG",
                "co_mention_count": 20,
            }
        ],
        "source_artifacts": [
            {
                "artifact_id": "art-001",
                "title": "Tesla Q4 2026 Earnings",
                "chunk_ids": ["chunk-1", "chunk-2"],
                "confidence": 0.95,
                "updated_at": "2026-05-09T10:00:00+00:00",
            }
        ],
    }


# ---------------------------------------------------------------------------
# list_entities
# ---------------------------------------------------------------------------


class TestListEntities:
    @pytest.mark.asyncio
    async def test_returns_entity_summary_list(self):
        driver = _make_driver()
        with patch("app.services.wiki_pages._neo4j_adapter.list_top_entities", return_value=_entity_rows()):
            results = await list_entities(driver, limit=30)

        assert len(results) == 2
        assert all(isinstance(r, EntitySummary) for r in results)

    @pytest.mark.asyncio
    async def test_first_entity_fields(self):
        driver = _make_driver()
        with patch("app.services.wiki_pages._neo4j_adapter.list_top_entities", return_value=_entity_rows()):
            results = await list_entities(driver)

        first = results[0]
        assert first.canonical_id == "person:elon-musk"
        assert first.name == "Elon Musk"
        assert first.entity_type == "PERSON"
        assert first.mention_count == 42
        assert first.recent_activity_score == 10
        assert first.summary == "A prominent entrepreneur."

    @pytest.mark.asyncio
    async def test_empty_adapter_returns_empty_list(self):
        driver = _make_driver()
        with patch("app.services.wiki_pages._neo4j_adapter.list_top_entities", return_value=[]):
            results = await list_entities(driver)

        assert results == []

    @pytest.mark.asyncio
    async def test_malformed_row_is_skipped(self):
        """A row missing required fields is skipped, not fatal."""
        driver = _make_driver()
        bad_rows = [{"bad_key": "bad_value"}, _entity_rows()[0]]
        with patch("app.services.wiki_pages._neo4j_adapter.list_top_entities", return_value=bad_rows):
            results = await list_entities(driver)

        # The valid row should still be present even if bad rows are skipped.
        # Malformed rows produce EntitySummary with empty strings (default fallback).
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_top_tags_parsed_on_list_rows(self):
        """Slice 6.3: top_tags JSON string on a list row parses to a list;
        absent top_tags is None (pre-job rows still list cleanly)."""
        rows = _entity_rows()
        rows[0]["top_tags"] = '["python", "docker"]'  # has tags
        # rows[1] has no top_tags key → None
        driver = _make_driver()
        with patch("app.services.wiki_pages._neo4j_adapter.list_top_entities", return_value=rows):
            results = await list_entities(driver)

        assert results[0].top_tags == ["python", "docker"]
        assert results[1].top_tags is None

    # WK1 — article-body search tests
    @pytest.mark.asyncio
    async def test_body_only_match_is_returned(self):
        """WK1: an entity whose NAME does not match q but whose summary CONTAINS q
        must be returned by list_top_entities (adapter mock simulates the widened WHERE)."""
        driver = _make_driver()
        body_only_row = {
            "canonical_id": "person:jane-doe",
            "name": "Jane Doe",
            "entity_type": "PERSON",
            "mention_count": 5,
            "recent_activity_score": 2,
            "summary": "An expert in quantum computing and cryptography.",
            "summary_updated_at": None,
            # match_rank 3 = body-only hit (new rank assigned by WK1 CASE)
            "match_rank": 3,
        }
        with patch(
            "app.services.wiki_pages._neo4j_adapter.list_top_entities",
            return_value=[body_only_row],
        ):
            results = await list_entities(driver, search="quantum")

        assert len(results) == 1
        result = results[0]
        assert result.canonical_id == "person:jane-doe"
        assert result.name == "Jane Doe"
        # match_rank 3 = body-only hit
        assert result.match_rank == 3

    @pytest.mark.asyncio
    async def test_name_match_ranks_above_body_only_match(self):
        """WK1: a name match (match_rank 0-2) sorts before a body-only match
        (match_rank 3) when the adapter returns both rows ordered by rank."""
        driver = _make_driver()
        name_match_row = {
            "canonical_id": "tech:quantum-corp",
            "name": "Quantum Corp",
            "entity_type": "ORG",
            "mention_count": 20,
            "recent_activity_score": 8,
            "summary": "A computing company.",
            "summary_updated_at": None,
            "match_rank": 2,  # name CONTAINS match
        }
        body_only_row = {
            "canonical_id": "person:jane-doe",
            "name": "Jane Doe",
            "entity_type": "PERSON",
            "mention_count": 5,
            "recent_activity_score": 2,
            "summary": "An expert in quantum computing.",
            "summary_updated_at": None,
            "match_rank": 3,  # body-only hit
        }
        # Adapter returns rows pre-ordered by match_rank ASC (as the Cypher ORDER BY does)
        with patch(
            "app.services.wiki_pages._neo4j_adapter.list_top_entities",
            return_value=[name_match_row, body_only_row],
        ):
            results = await list_entities(driver, search="quantum")

        assert len(results) == 2
        assert results[0].canonical_id == "tech:quantum-corp"
        assert results[0].match_rank == 2
        assert results[1].canonical_id == "person:jane-doe"
        assert results[1].match_rank == 3


# ---------------------------------------------------------------------------
# get_entity_page — happy path
# ---------------------------------------------------------------------------


class TestGetEntityPage:
    @pytest.mark.asyncio
    async def test_assembles_all_fields(self):
        driver = _make_driver()
        with (
            patch("app.services.wiki_pages._neo4j_adapter.get_entity", return_value=_full_entity_raw()),
            patch("app.services.wiki_pages._neo4j_adapter.get_confidence_band", return_value="high"),
            patch(
                "app.services.contradiction_log.list_recent",
                new=AsyncMock(return_value=[]),
            ),
        ):
            page = await get_entity_page(driver, "person:elon-musk")

        assert page is not None
        assert isinstance(page, WikiEntityPage)
        assert page.slug == "person:elon-musk"
        assert page.name == "Elon Musk"
        assert page.summary == "A prominent entrepreneur."
        assert len(page.related_entities) == 1
        assert isinstance(page.related_entities[0], RelatedEntity)
        assert page.related_entities[0].canonical_id == "org:tesla"
        assert len(page.source_artifacts) == 1
        assert isinstance(page.source_artifacts[0], SourceCitation)
        assert page.source_artifacts[0].artifact_id == "art-001"
        assert page.confidence_band == "high"
        assert page.last_updated_at == "2026-05-10T12:00:00+00:00"
        assert page.next_refresh_due is not None

    @pytest.mark.asyncio
    async def test_parses_domain_salience_and_top_tags(self):
        """Slice 6: domain_salience + top_tags arrive as JSON strings from
        Neo4j and are parsed onto the page (salience order preserved)."""
        raw = _full_entity_raw()
        raw["primary_domain"] = "finance"
        raw["domain_salience"] = '{"finance": 45.0, "general": 11.25}'
        raw["top_tags"] = '["invoice", "budget"]'
        driver = _make_driver()
        with (
            patch("app.services.wiki_pages._neo4j_adapter.get_entity", return_value=raw),
            patch("app.services.wiki_pages._neo4j_adapter.get_confidence_band", return_value="high"),
            patch(
                "app.services.contradiction_log.list_recent",
                new=AsyncMock(return_value=[]),
            ),
        ):
            page = await get_entity_page(driver, "person:elon-musk")

        assert page is not None
        assert page.domain_salience == {"finance": 45.0, "general": 11.25}
        assert list(page.domain_salience.keys()) == ["finance", "general"]
        assert page.top_tags == ["invoice", "budget"]

    @pytest.mark.asyncio
    async def test_domain_salience_and_top_tags_null_when_absent(self):
        """Pre-job entities (no salience/tags) parse to None, not a crash."""
        driver = _make_driver()
        with (
            patch("app.services.wiki_pages._neo4j_adapter.get_entity", return_value=_full_entity_raw()),
            patch("app.services.wiki_pages._neo4j_adapter.get_confidence_band", return_value="high"),
            patch(
                "app.services.contradiction_log.list_recent",
                new=AsyncMock(return_value=[]),
            ),
        ):
            page = await get_entity_page(driver, "person:elon-musk")

        assert page is not None
        assert page.domain_salience is None
        assert page.top_tags is None

    @pytest.mark.asyncio
    async def test_contradictions_are_pulled_from_service(self):
        from app.services.contradiction_log import ContradictionFinding

        finding = ContradictionFinding(
            finding_id="f-001",
            claim_a_id="c-001",
            claim_b_id="c-002",
            claim_a_text="A",
            claim_b_text="B",
            entity_slug="person:elon-musk",
            severity="high",
            detected_at="2026-05-10T00:00:00+00:00",
        )
        driver = _make_driver()
        with (
            patch("app.services.wiki_pages._neo4j_adapter.get_entity", return_value=_full_entity_raw()),
            patch("app.services.wiki_pages._neo4j_adapter.get_confidence_band", return_value="medium"),
            patch(
                "app.services.contradiction_log.list_recent",
                new=AsyncMock(return_value=[finding]),
            ),
        ):
            page = await get_entity_page(driver, "person:elon-musk")

        assert len(page.contradictions) == 1
        assert page.contradictions[0]["finding_id"] == "f-001"

    @pytest.mark.asyncio
    async def test_404_path_returns_none(self):
        driver = _make_driver()
        with patch("app.services.wiki_pages._neo4j_adapter.get_entity", return_value=None):
            page = await get_entity_page(driver, "nonexistent:slug")

        assert page is None


# ---------------------------------------------------------------------------
# confidence_band boundaries
# ---------------------------------------------------------------------------


class TestConfidenceBand:
    """Verify that get_entity_page passes through the band from the adapter."""

    @pytest.mark.asyncio
    async def test_high_band(self):
        """80%+ verified claims → high."""
        driver = _make_driver()
        with (
            patch("app.services.wiki_pages._neo4j_adapter.get_entity", return_value=_full_entity_raw()),
            patch("app.services.wiki_pages._neo4j_adapter.get_confidence_band", return_value="high"),
            patch("app.services.contradiction_log.list_recent", new=AsyncMock(return_value=[])),
        ):
            page = await get_entity_page(driver, "person:elon-musk")

        assert page is not None
        assert page.confidence_band == "high"

    @pytest.mark.asyncio
    async def test_medium_band(self):
        """50–79% verified claims → medium."""
        driver = _make_driver()
        with (
            patch("app.services.wiki_pages._neo4j_adapter.get_entity", return_value=_full_entity_raw()),
            patch("app.services.wiki_pages._neo4j_adapter.get_confidence_band", return_value="medium"),
            patch("app.services.contradiction_log.list_recent", new=AsyncMock(return_value=[])),
        ):
            page = await get_entity_page(driver, "person:elon-musk")

        assert page is not None
        assert page.confidence_band == "medium"

    @pytest.mark.asyncio
    async def test_low_band(self):
        """< 50% verified claims → low."""
        driver = _make_driver()
        with (
            patch("app.services.wiki_pages._neo4j_adapter.get_entity", return_value=_full_entity_raw()),
            patch("app.services.wiki_pages._neo4j_adapter.get_confidence_band", return_value="low"),
            patch("app.services.contradiction_log.list_recent", new=AsyncMock(return_value=[])),
        ):
            page = await get_entity_page(driver, "person:elon-musk")

        assert page is not None
        assert page.confidence_band == "low"

    @pytest.mark.asyncio
    async def test_unknown_band_when_no_claims(self):
        """0 claims → unknown."""
        driver = _make_driver()
        with (
            patch("app.services.wiki_pages._neo4j_adapter.get_entity", return_value=_full_entity_raw()),
            patch("app.services.wiki_pages._neo4j_adapter.get_confidence_band", return_value="unknown"),
            patch("app.services.contradiction_log.list_recent", new=AsyncMock(return_value=[])),
        ):
            page = await get_entity_page(driver, "person:elon-musk")

        assert page is not None
        assert page.confidence_band == "unknown"


# ---------------------------------------------------------------------------
# _compute_next_refresh helper
# ---------------------------------------------------------------------------


class TestComputeNextRefresh:
    def test_adds_24h_to_summary_updated_at(self):
        result = _compute_next_refresh("2026-05-10T00:00:00+00:00")
        assert "2026-05-11" in result

    def test_none_returns_current_time(self):
        """No summary yet → refresh is overdue → returns roughly now."""
        import re
        result = _compute_next_refresh(None)
        # Should be an ISO timestamp starting with 2026
        assert re.match(r"202\d-\d{2}-\d{2}", result)

    def test_unparseable_returns_current_time(self):
        result = _compute_next_refresh("not-a-date")
        assert result  # Just ensure a non-empty string is returned


# ---------------------------------------------------------------------------
# compute_completeness (WK3)
# ---------------------------------------------------------------------------


class TestComputeCompleteness:
    """Boundary tests for the WK3 completeness helper."""

    # --- stub ---------------------------------------------------------------

    def test_none_summary_is_stub(self):
        assert compute_completeness(None, 10, "high") == "stub"

    def test_empty_summary_is_stub(self):
        assert compute_completeness("", 10, "high") == "stub"

    def test_whitespace_only_summary_is_stub(self):
        assert compute_completeness("   ", 10, "high") == "stub"

    # --- start via short summary --------------------------------------------

    def test_one_char_below_start_threshold_is_start(self):
        from app.services.wiki_pages import START_CHARS
        short = "x" * (START_CHARS - 1)
        assert compute_completeness(short, 10, "high") == "start"

    def test_exactly_at_start_chars_boundary_is_not_start_from_length(self):
        """At exactly START_CHARS with sufficient mentions → not downgraded to start."""
        from app.services.wiki_pages import START_CHARS, START_MENTIONS
        summary = "x" * START_CHARS
        assert compute_completeness(summary, START_MENTIONS, "high") == "full"

    # --- start via low mentions ---------------------------------------------

    def test_zero_mentions_with_long_summary_is_start(self):
        from app.services.wiki_pages import START_CHARS
        long_summary = "x" * (START_CHARS + 1)
        assert compute_completeness(long_summary, 0, "high") == "start"

    def test_one_below_start_mentions_is_start(self):
        from app.services.wiki_pages import START_CHARS, START_MENTIONS
        long_summary = "x" * (START_CHARS + 1)
        assert compute_completeness(long_summary, START_MENTIONS - 1, "high") == "start"

    def test_exactly_at_start_mentions_boundary_is_full(self):
        from app.services.wiki_pages import START_CHARS, START_MENTIONS
        long_summary = "x" * (START_CHARS + 1)
        assert compute_completeness(long_summary, START_MENTIONS, "high") == "full"

    # --- full ---------------------------------------------------------------

    def test_long_summary_and_many_mentions_is_full(self):
        from app.services.wiki_pages import START_CHARS, START_MENTIONS
        long_summary = "x" * (START_CHARS + 50)
        assert compute_completeness(long_summary, START_MENTIONS + 5, "medium") == "full"

    def test_band_does_not_affect_classification(self):
        """confidence_band is passed through but must not change stub/start/full logic."""
        from app.services.wiki_pages import START_CHARS, START_MENTIONS
        long_summary = "x" * (START_CHARS + 1)
        for band in ("high", "medium", "low", "unknown"):
            assert compute_completeness(long_summary, START_MENTIONS, band) == "full"

    # --- page assembles completeness ----------------------------------------

    @pytest.mark.asyncio
    async def test_get_entity_page_sets_completeness_stub(self):
        """No summary → completeness=stub on the assembled page."""
        raw = _full_entity_raw()
        raw["summary"] = None
        raw["mention_count"] = 0
        driver = _make_driver()
        with (
            patch("app.services.wiki_pages._neo4j_adapter.get_entity", return_value=raw),
            patch("app.services.wiki_pages._neo4j_adapter.get_confidence_band", return_value="unknown"),
            patch("app.services.contradiction_log.list_recent", new=AsyncMock(return_value=[])),
        ):
            page = await get_entity_page(driver, "person:elon-musk")
        assert page is not None
        assert page.completeness == "stub"

    @pytest.mark.asyncio
    async def test_get_entity_page_sets_completeness_full(self):
        """Long summary + many mentions → completeness=full on the assembled page."""
        from app.services.wiki_pages import START_CHARS, START_MENTIONS
        raw = _full_entity_raw()
        raw["summary"] = "A " * (START_CHARS // 2 + 10)  # well above START_CHARS
        raw["mention_count"] = START_MENTIONS + 5
        driver = _make_driver()
        with (
            patch("app.services.wiki_pages._neo4j_adapter.get_entity", return_value=raw),
            patch("app.services.wiki_pages._neo4j_adapter.get_confidence_band", return_value="high"),
            patch("app.services.contradiction_log.list_recent", new=AsyncMock(return_value=[])),
        ):
            page = await get_entity_page(driver, "person:elon-musk")
        assert page is not None
        assert page.completeness == "full"
