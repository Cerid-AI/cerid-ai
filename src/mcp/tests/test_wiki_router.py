# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""FastAPI TestClient tests for the wiki router (Phase W.1).

Mounts only the wiki router against a minimal FastAPI app so no live
infrastructure (Neo4j, Redis, ChromaDB) is required. The service layer
is patched at the router import boundary.

Coverage:
- GET /wiki/entities?limit=10  → list shape
- GET /wiki/entities/{slug}    → full WikiEntityPage happy path
- GET /wiki/entities/{slug}    → 404 path
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.wiki import router
from app.services.wiki_pages import (
    EntitySummary,
    RelatedEntity,
    SourceCitation,
    WikiEntityPage,
)

# ---------------------------------------------------------------------------
# Minimal test app
# ---------------------------------------------------------------------------

_test_app = FastAPI()
_test_app.include_router(router)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_test_app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entity_summaries(n: int = 2) -> list[EntitySummary]:
    return [
        EntitySummary(
            canonical_id=f"person:entity-{i}",
            name=f"Entity {i}",
            entity_type="PERSON",
            mention_count=10 * i,
            recent_activity_score=5 * i,
            summary=f"Summary {i}" if i % 2 == 0 else None,
        )
        for i in range(1, n + 1)
    ]


def _make_wiki_page() -> WikiEntityPage:
    return WikiEntityPage(
        slug="person:elon-musk",
        name="Elon Musk",
        entity_type="PERSON",
        summary="A prominent tech entrepreneur.",
        related_entities=[
            RelatedEntity(
                canonical_id="org:tesla",
                name="Tesla",
                entity_type="ORG",
                co_mention_count=20,
            )
        ],
        source_artifacts=[
            SourceCitation(
                artifact_id="art-001",
                title="Tesla Earnings",
                chunk_ids=["c-1", "c-2"],
                confidence=0.95,
                updated_at="2026-05-09T10:00:00+00:00",
            )
        ],
        contradictions=[],
        last_updated_at="2026-05-10T12:00:00+00:00",
        next_refresh_due="2026-05-11T12:00:00+00:00",
        confidence_band="high",
    )


# ---------------------------------------------------------------------------
# GET /wiki/entities
# ---------------------------------------------------------------------------


class TestListEntityPages:
    def test_returns_200_with_list(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=None),
            patch(
                "app.routers.wiki.list_entities",
                new=AsyncMock(return_value=_make_entity_summaries(2)),
            ),
        ):
            resp = client.get("/wiki/entities?limit=10")

        assert resp.status_code == 200

    def test_response_is_list(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=None),
            patch(
                "app.routers.wiki.list_entities",
                new=AsyncMock(return_value=_make_entity_summaries(2)),
            ),
        ):
            resp = client.get("/wiki/entities?limit=10")

        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2

    def test_entity_summary_fields(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=None),
            patch(
                "app.routers.wiki.list_entities",
                new=AsyncMock(return_value=_make_entity_summaries(1)),
            ),
        ):
            resp = client.get("/wiki/entities")

        item = resp.json()[0]
        assert "canonical_id" in item
        assert "name" in item
        assert "entity_type" in item
        assert "mention_count" in item
        assert "recent_activity_score" in item

    def test_limit_query_param_is_forwarded(self, client: TestClient):
        """Confirm that the limit parameter is passed through."""
        captured: list[Any] = []

        async def _mock_list(
            driver: Any, *, limit: int = 30, search: str | None = None
        ) -> list[EntitySummary]:
            captured.append(limit)
            return []

        with (
            patch("app.deps.get_neo4j", return_value=None),
            patch("app.routers.wiki.list_entities", new=_mock_list),
        ):
            client.get("/wiki/entities?limit=15")

        assert captured == [15]

    def test_search_query_param_is_forwarded(self, client: TestClient):
        """The q param reaches list_entities as `search` (F5 server-side search)."""
        captured: list[Any] = []

        async def _mock_list(
            driver: Any, *, limit: int = 30, search: str | None = None
        ) -> list[EntitySummary]:
            captured.append(search)
            return []

        with (
            patch("app.deps.get_neo4j", return_value=None),
            patch("app.routers.wiki.list_entities", new=_mock_list),
        ):
            client.get("/wiki/entities?limit=50&q=ethereum")

        assert captured == ["ethereum"]

    def test_empty_list_returns_200(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=None),
            patch(
                "app.routers.wiki.list_entities",
                new=AsyncMock(return_value=[]),
            ),
        ):
            resp = client.get("/wiki/entities")

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /wiki/entities/{slug} — happy path
# ---------------------------------------------------------------------------


class TestGetEntityWikiPage:
    def test_returns_200_on_found(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=None),
            patch(
                "app.routers.wiki.get_entity_page",
                new=AsyncMock(return_value=_make_wiki_page()),
            ),
        ):
            resp = client.get("/wiki/entities/person:elon-musk")

        assert resp.status_code == 200

    def test_documented_shape(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=None),
            patch(
                "app.routers.wiki.get_entity_page",
                new=AsyncMock(return_value=_make_wiki_page()),
            ),
        ):
            resp = client.get("/wiki/entities/person:elon-musk")

        data = resp.json()
        assert data["slug"] == "person:elon-musk"
        assert data["name"] == "Elon Musk"
        assert "summary" in data
        assert "related_entities" in data
        assert "source_artifacts" in data
        assert "contradictions" in data
        assert "last_updated_at" in data
        assert "next_refresh_due" in data
        assert "confidence_band" in data

    def test_related_entities_shape(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=None),
            patch(
                "app.routers.wiki.get_entity_page",
                new=AsyncMock(return_value=_make_wiki_page()),
            ),
        ):
            resp = client.get("/wiki/entities/person:elon-musk")

        related = resp.json()["related_entities"]
        assert len(related) == 1
        assert related[0]["canonical_id"] == "org:tesla"
        assert related[0]["co_mention_count"] == 20

    def test_source_artifacts_shape(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=None),
            patch(
                "app.routers.wiki.get_entity_page",
                new=AsyncMock(return_value=_make_wiki_page()),
            ),
        ):
            resp = client.get("/wiki/entities/person:elon-musk")

        artifacts = resp.json()["source_artifacts"]
        assert len(artifacts) == 1
        assert artifacts[0]["artifact_id"] == "art-001"
        assert "chunk_ids" in artifacts[0]


# ---------------------------------------------------------------------------
# GET /wiki/entities/{slug} — 404 path
# ---------------------------------------------------------------------------


class TestGetEntityWikiPage404:
    def test_returns_404_when_not_found(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=None),
            patch(
                "app.routers.wiki.get_entity_page",
                new=AsyncMock(return_value=None),
            ),
        ):
            resp = client.get("/wiki/entities/nonexistent:slug")

        assert resp.status_code == 404

    def test_404_detail_contains_slug(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=None),
            patch(
                "app.routers.wiki.get_entity_page",
                new=AsyncMock(return_value=None),
            ),
        ):
            resp = client.get("/wiki/entities/nonexistent:slug")

        assert "nonexistent:slug" in resp.json()["detail"]
