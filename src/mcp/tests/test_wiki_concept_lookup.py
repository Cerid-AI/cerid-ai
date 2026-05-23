# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase K5 — concept (community) page lookup via pkb_wiki_lookup."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_community(community_id: str = "0:42", with_summary: bool = True):
    class _Community:
        def __init__(self, data):
            self._data = data

        def model_dump(self):
            return dict(self._data)

    return _Community({
        "id": community_id,
        "level": 0,
        "native_id": int(community_id.split(":")[1]),
        "title": "AI safety researchers",
        "summary": ("This community clusters researchers working on AI alignment "
                    "and safety, including affiliations with Anthropic, DeepMind, "
                    "and academic labs.") if with_summary else None,
        "summary_generated_at": "2026-05-20T10:00:00Z",
        "member_count": 17,
        "members": [
            {"canonical_id": "person:dario-amodei", "name": "Dario Amodei"},
            {"canonical_id": "person:demis-hassabis", "name": "Demis Hassabis"},
        ],
    })


class TestConceptSlugDetection:
    @pytest.mark.parametrize("query,expected", [
        ("concept:0:42", "0:42"),
        ("concept:1:7", "1:7"),
        ("0:42", "0:42"),
        ("1:23", "1:23"),
    ])
    def test_recognises_concept_slug(self, query, expected):
        from app.mcp_tools.wiki import _strip_concept_prefix

        assert _strip_concept_prefix(query) == expected

    @pytest.mark.parametrize("query", [
        "org:tesla",
        "Tesla",
        "person:elon-musk",
        "concept:not-a-community",
        "what is concept",
    ])
    def test_rejects_non_concept(self, query):
        from app.mcp_tools.wiki import _strip_concept_prefix

        assert _strip_concept_prefix(query) is None


class TestConceptLookup:
    @pytest.mark.asyncio
    async def test_summary_depth_returns_concept_envelope(self):
        from app.mcp_tools.wiki import pkb_wiki_lookup

        community = _make_community()
        mock_driver = MagicMock()
        with (
            patch("app.mcp_tools.wiki.get_neo4j", return_value=mock_driver),
            patch(
                "app.services.community_pages.get_community_page",
                new=AsyncMock(return_value=community),
            ),
        ):
            result = await pkb_wiki_lookup("concept:0:42", depth="summary")

        assert result["found"] is True
        assert result["kind"] == "concept"
        page = result["page"]
        assert page["slug"] == "concept:0:42"
        assert page["entity_type"] == "CONCEPT"
        assert "AI alignment" in page["summary"]
        # summary depth: members not included
        assert "members" not in page

    @pytest.mark.asyncio
    async def test_full_depth_includes_members(self):
        from app.mcp_tools.wiki import pkb_wiki_lookup

        community = _make_community()
        mock_driver = MagicMock()
        with (
            patch("app.mcp_tools.wiki.get_neo4j", return_value=mock_driver),
            patch(
                "app.services.community_pages.get_community_page",
                new=AsyncMock(return_value=community),
            ),
        ):
            result = await pkb_wiki_lookup("concept:0:42", depth="full")

        assert result["page"]["member_count"] == 17
        assert len(result["page"]["members"]) == 2

    @pytest.mark.asyncio
    async def test_bare_community_id_also_resolves(self):
        from app.mcp_tools.wiki import pkb_wiki_lookup

        community = _make_community("0:99")
        mock_driver = MagicMock()
        with (
            patch("app.mcp_tools.wiki.get_neo4j", return_value=mock_driver),
            patch(
                "app.services.community_pages.get_community_page",
                new=AsyncMock(return_value=community),
            ),
        ):
            result = await pkb_wiki_lookup("0:99", depth="summary")

        assert result["found"] is True
        assert result["kind"] == "concept"
        assert result["page"]["slug"] == "concept:0:99"

    @pytest.mark.asyncio
    async def test_concept_miss_raises_not_found(self):
        from app.mcp_tools.wiki import pkb_wiki_lookup
        from app.tool_registry import ResourceNotFoundError

        mock_driver = MagicMock()
        with (
            patch("app.mcp_tools.wiki.get_neo4j", return_value=mock_driver),
            patch(
                "app.services.community_pages.get_community_page",
                new=AsyncMock(return_value=None),
            ),
        ):
            with pytest.raises(ResourceNotFoundError):
                await pkb_wiki_lookup("concept:0:999", depth="summary")
