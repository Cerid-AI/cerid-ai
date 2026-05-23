# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the pkb_wiki_lookup MCP tool (Phase K1.5)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_page(slug: str = "org:tesla", with_externals: bool = True):
    """Build a synthetic WikiEntityPage model_dump-style dict + a model stand-in."""

    class _Page:
        def __init__(self, data):
            self._data = data

        def model_dump(self):
            return dict(self._data)

    return _Page({
        "slug": slug,
        "name": "Tesla",
        "entity_type": "ORG",
        "summary": "Tesla is an electric vehicle manufacturer.",
        "related_entities": [{"canonical_id": "person:elon-musk", "name": "Elon Musk", "entity_type": "PER", "co_mention_count": 12}],
        "source_artifacts": [{"artifact_id": "a1", "title": "10-K filing", "chunk_ids": ["c1"], "confidence": 0.8, "updated_at": "2026-05-01T00:00:00Z"}],
        "contradictions": [],
        "external_references": (
            [{"source": "wikipedia", "source_display": "Wikipedia", "title": "Tesla, Inc.", "snippet": "An American electric vehicle...", "url": "https://en.wikipedia.org/wiki/Tesla,_Inc.", "fetched_at": "2026-05-15T00:00:00Z", "metadata": {}}]
            if with_externals else []
        ),
        "last_updated_at": "2026-05-22T12:00:00Z",
        "next_refresh_due": "2026-05-23T12:00:00Z",
        "confidence_band": "medium",
    })


class TestWikiLookupTool:
    @pytest.mark.asyncio
    async def test_summary_depth_strips_heavy_fields(self):
        from app.mcp_tools.wiki import pkb_wiki_lookup

        page = _make_page()
        mock_driver = MagicMock()
        with (
            patch("app.mcp_tools.wiki.get_neo4j", return_value=mock_driver),
            patch("app.services.wiki_pages.get_entity_page", new=AsyncMock(return_value=page)),
        ):
            result = await pkb_wiki_lookup("org:tesla", depth="summary")

        assert result["found"] is True
        keys = set(result["page"].keys())
        # Summary depth keeps only the lightweight fields
        assert "summary" in keys
        assert "confidence_band" in keys
        assert "related_entities" not in keys
        assert "source_artifacts" not in keys
        assert "external_references" not in keys

    @pytest.mark.asyncio
    async def test_full_depth_includes_relations_but_not_externals(self):
        from app.mcp_tools.wiki import pkb_wiki_lookup

        page = _make_page()
        mock_driver = MagicMock()
        with (
            patch("app.mcp_tools.wiki.get_neo4j", return_value=mock_driver),
            patch("app.services.wiki_pages.get_entity_page", new=AsyncMock(return_value=page)),
        ):
            result = await pkb_wiki_lookup("org:tesla", depth="full")

        keys = set(result["page"].keys())
        assert "related_entities" in keys
        assert "source_artifacts" in keys
        assert "external_references" not in keys

    @pytest.mark.asyncio
    async def test_with_refs_includes_externals(self):
        from app.mcp_tools.wiki import pkb_wiki_lookup

        page = _make_page()
        mock_driver = MagicMock()
        with (
            patch("app.mcp_tools.wiki.get_neo4j", return_value=mock_driver),
            patch("app.services.wiki_pages.get_entity_page", new=AsyncMock(return_value=page)),
        ):
            result = await pkb_wiki_lookup("org:tesla", depth="with_refs")

        assert "external_references" in result["page"]
        assert len(result["page"]["external_references"]) == 1

    @pytest.mark.asyncio
    async def test_fuzzy_match_resolves_single_candidate(self):
        from app.mcp_tools.wiki import pkb_wiki_lookup

        page = _make_page()
        mock_driver = MagicMock()

        # Direct lookup misses; fuzzy returns one summary-bearing candidate
        get_page = AsyncMock(side_effect=[None, page])

        def _fuzzy(_driver, _query, *, limit=5):
            return [{
                "slug": "org:tesla", "name": "Tesla", "entity_type": "ORG",
                "mention_count": 50, "has_summary": True,
            }]

        with (
            patch("app.mcp_tools.wiki.get_neo4j", return_value=mock_driver),
            patch("app.services.wiki_pages.get_entity_page", new=get_page),
            patch("app.mcp_tools.wiki._fuzzy_match_slug", _fuzzy),
        ):
            result = await pkb_wiki_lookup("Tesla", depth="summary")

        assert result["found"] is True
        assert result["page"]["slug"] == "org:tesla"

    @pytest.mark.asyncio
    async def test_fuzzy_match_no_candidate_raises(self):
        from app.mcp_tools.wiki import pkb_wiki_lookup
        from app.tool_registry import ResourceNotFoundError

        mock_driver = MagicMock()
        with (
            patch("app.mcp_tools.wiki.get_neo4j", return_value=mock_driver),
            patch("app.services.wiki_pages.get_entity_page", new=AsyncMock(return_value=None)),
            patch("app.mcp_tools.wiki._fuzzy_match_slug", lambda *a, **kw: []),
        ):
            with pytest.raises(ResourceNotFoundError):
                await pkb_wiki_lookup("nonexistent", depth="summary")

    @pytest.mark.asyncio
    async def test_invalid_depth_raises(self):
        from app.mcp_tools.wiki import pkb_wiki_lookup
        from app.tool_registry import InvalidParamsError

        with pytest.raises(InvalidParamsError):
            await pkb_wiki_lookup("org:tesla", depth="bogus")

    @pytest.mark.asyncio
    async def test_empty_query_raises(self):
        from app.mcp_tools.wiki import pkb_wiki_lookup
        from app.tool_registry import InvalidParamsError

        with pytest.raises(InvalidParamsError):
            await pkb_wiki_lookup("   ", depth="summary")

    @pytest.mark.asyncio
    async def test_neo4j_unavailable_raises_upstream(self):
        from app.mcp_tools.wiki import pkb_wiki_lookup
        from app.tool_registry import UpstreamUnavailableError

        with patch("app.mcp_tools.wiki.get_neo4j", return_value=None):
            with pytest.raises(UpstreamUnavailableError):
                await pkb_wiki_lookup("org:tesla", depth="summary")
