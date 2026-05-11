# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the community pages service (Phase R.2).

All Neo4j calls are mocked at the adapter boundary.  No live database
required.

Coverage:
- list_top_communities: returns sorted CommunitySummary list
- list_top_communities: empty adapter → empty list
- list_top_communities: min_size filtering honoured by adapter
- get_community_page: assembles full CommunityFull (members + related)
- get_community_page: None on missing community (404 path)
- Pydantic round-trip: CommunitySummary and CommunityFull serialise clean
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.community_pages import (
    CommunitySummary,
    CommunityFull,
    get_community_page,
    list_top_communities,
)
from app.db.neo4j.communities import MemberEntity, RelatedCommunity


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_driver() -> MagicMock:
    """Stub Neo4j driver — never actually called (adapter is patched)."""
    return MagicMock()


def _summary_rows() -> list[dict[str, Any]]:
    return [
        CommunitySummary(
            community_id="0:7",
            level=0,
            summary="Machine learning research community centred on transformers.",
            member_count=25,
            last_summarized_at="2026-05-10T02:00:00+00:00",
        ),
        CommunitySummary(
            community_id="0:3",
            level=0,
            summary="Financial markets and trading entities.",
            member_count=12,
            last_summarized_at="2026-05-10T02:05:00+00:00",
        ),
    ]


def _full_community() -> CommunityFull:
    return CommunityFull(
        community_id="0:7",
        level=0,
        summary="Machine learning research community centred on transformers.",
        member_count=25,
        last_summarized_at="2026-05-10T02:00:00+00:00",
        members=[
            MemberEntity(canonical_id="person:yann-lecun", name="Yann LeCun", entity_type="PERSON"),
            MemberEntity(canonical_id="org:meta-ai", name="Meta AI", entity_type="ORG"),
        ],
        related_communities=[
            RelatedCommunity(community_id="0:4", co_mention_count=38),
        ],
    )


# ---------------------------------------------------------------------------
# list_top_communities
# ---------------------------------------------------------------------------


class TestListTopCommunities:
    @pytest.mark.asyncio
    async def test_returns_community_summary_list(self):
        driver = _make_driver()
        rows = _summary_rows()
        with patch(
            "app.services.community_pages.list_communities",
            return_value=rows,
        ):
            result = await list_top_communities(driver, min_size=3, limit=30)

        assert len(result) == 2
        assert all(isinstance(r, CommunitySummary) for r in result)

    @pytest.mark.asyncio
    async def test_first_row_fields(self):
        driver = _make_driver()
        with patch(
            "app.services.community_pages.list_communities",
            return_value=_summary_rows(),
        ):
            result = await list_top_communities(driver)

        first = result[0]
        assert first.community_id == "0:7"
        assert first.member_count == 25
        assert first.summary is not None
        assert "transformer" in first.summary.lower()

    @pytest.mark.asyncio
    async def test_empty_adapter_returns_empty_list(self):
        driver = _make_driver()
        with patch(
            "app.services.community_pages.list_communities",
            return_value=[],
        ):
            result = await list_top_communities(driver)

        assert result == []

    @pytest.mark.asyncio
    async def test_min_size_passed_to_adapter(self):
        """min_size is forwarded to the adapter unmodified."""
        driver = _make_driver()
        with patch(
            "app.services.community_pages.list_communities",
            return_value=[],
        ) as mock_list:
            await list_top_communities(driver, min_size=10, limit=5)

        mock_list.assert_called_once_with(driver, min_size=10, limit=5, level=0)

    @pytest.mark.asyncio
    async def test_limit_passed_to_adapter(self):
        driver = _make_driver()
        with patch(
            "app.services.community_pages.list_communities",
            return_value=[],
        ) as mock_list:
            await list_top_communities(driver, min_size=3, limit=10)

        mock_list.assert_called_once_with(driver, min_size=3, limit=10, level=0)


# ---------------------------------------------------------------------------
# get_community_page — happy path
# ---------------------------------------------------------------------------


class TestGetCommunityPage:
    @pytest.mark.asyncio
    async def test_happy_path_returns_community_full(self):
        driver = _make_driver()
        expected = _full_community()
        with patch(
            "app.services.community_pages.get_community",
            return_value=expected,
        ):
            result = await get_community_page(driver, "0:7")

        assert result is not None
        assert isinstance(result, CommunityFull)
        assert result.community_id == "0:7"
        assert result.level == 0
        assert result.member_count == 25
        assert len(result.members) == 2
        assert result.members[0].canonical_id == "person:yann-lecun"
        assert len(result.related_communities) == 1
        assert result.related_communities[0].community_id == "0:4"

    @pytest.mark.asyncio
    async def test_community_id_forwarded_to_adapter(self):
        driver = _make_driver()
        with patch(
            "app.services.community_pages.get_community",
            return_value=None,
        ) as mock_get:
            await get_community_page(driver, "0:99")

        mock_get.assert_called_once_with(driver, "0:99")

    @pytest.mark.asyncio
    async def test_404_path_returns_none(self):
        driver = _make_driver()
        with patch(
            "app.services.community_pages.get_community",
            return_value=None,
        ):
            result = await get_community_page(driver, "0:nonexistent")

        assert result is None


# ---------------------------------------------------------------------------
# Pydantic round-trip
# ---------------------------------------------------------------------------


class TestPydanticRoundTrip:
    def test_community_summary_round_trips(self):
        row = CommunitySummary(
            community_id="0:7",
            level=0,
            summary="Test summary.",
            member_count=15,
            last_summarized_at="2026-05-10T00:00:00+00:00",
        )
        dumped = row.model_dump()
        restored = CommunitySummary(**dumped)
        assert restored == row

    def test_community_full_round_trips(self):
        full = _full_community()
        dumped = full.model_dump()
        restored = CommunityFull(**dumped)
        assert restored == full

    def test_community_summary_with_none_fields(self):
        """Ensure optional fields serialize to None, not omitted."""
        row = CommunitySummary(
            community_id="0:1",
            level=0,
            summary=None,
            member_count=3,
            last_summarized_at=None,
        )
        dumped = row.model_dump()
        assert dumped["summary"] is None
        assert dumped["last_summarized_at"] is None

    def test_community_full_empty_members(self):
        full = CommunityFull(
            community_id="0:2",
            level=0,
            summary=None,
            member_count=0,
            last_summarized_at=None,
            members=[],
            related_communities=[],
        )
        dumped = full.model_dump()
        assert dumped["members"] == []
        assert dumped["related_communities"] == []
