# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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
from unittest.mock import AsyncMock, MagicMock, patch

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
            driver: Any,
            *,
            limit: int = 30,
            search: str | None = None,
            include_internal: bool = False,
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
            driver: Any,
            *,
            limit: int = 30,
            search: str | None = None,
            include_internal: bool = False,
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


# ---------------------------------------------------------------------------
# GET /wiki/entities/{slug}/backlinks — WK1
# ---------------------------------------------------------------------------


class TestGetEntityBacklinks:
    """Tests for the 'what links here' backlinks endpoint."""

    def test_returns_200_with_backlinks(self, client: TestClient):
        backlinks = [
            {"slug": "org:tesla", "name": "Tesla", "entity_type": "ORG", "via": "wikilink"},
            {"slug": "org:spacex", "name": "SpaceX", "entity_type": "ORG", "via": "mention"},
        ]
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch(
                "app.routers.wiki.get_backlinks",
                return_value=backlinks,
            ),
        ):
            resp = client.get("/wiki/entities/person:elon-musk/backlinks")

        assert resp.status_code == 200

    def test_response_shape(self, client: TestClient):
        backlinks = [
            {"slug": "org:tesla", "name": "Tesla", "entity_type": "ORG", "via": "wikilink"},
        ]
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch(
                "app.routers.wiki.get_backlinks",
                return_value=backlinks,
            ),
        ):
            resp = client.get("/wiki/entities/person:elon-musk/backlinks")

        data = resp.json()
        assert "backlinks" in data
        assert isinstance(data["backlinks"], list)
        item = data["backlinks"][0]
        assert item["slug"] == "org:tesla"
        assert item["name"] == "Tesla"
        assert item["entity_type"] == "ORG"
        assert item["via"] == "wikilink"

    def test_empty_returns_empty_list(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch(
                "app.routers.wiki.get_backlinks",
                return_value=[],
            ),
        ):
            resp = client.get("/wiki/entities/person:nobody/backlinks")

        data = resp.json()
        assert resp.status_code == 200
        assert data["backlinks"] == []

    def test_via_values_are_valid(self, client: TestClient):
        backlinks = [
            {"slug": "a:b", "name": "B", "entity_type": "OTHER", "via": "wikilink"},
            {"slug": "a:c", "name": "C", "entity_type": "OTHER", "via": "mention"},
            {"slug": "a:d", "name": "D", "entity_type": "OTHER", "via": "related"},
        ]
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch(
                "app.routers.wiki.get_backlinks",
                return_value=backlinks,
            ),
        ):
            resp = client.get("/wiki/entities/a:target/backlinks")

        data = resp.json()
        valid_via = {"wikilink", "mention", "related"}
        for item in data["backlinks"]:
            assert item["via"] in valid_via

    def test_slug_is_forwarded_to_get_backlinks(self, client: TestClient):
        captured: list[Any] = []

        def _mock_get_backlinks(driver: Any, slug: str, limit: int = 50) -> list[dict[str, Any]]:
            captured.append(slug)
            return []

        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch("app.routers.wiki.get_backlinks", side_effect=_mock_get_backlinks),
        ):
            client.get("/wiki/entities/person:test-entity/backlinks")

        assert captured == ["person:test-entity"]

    def test_neo4j_unavailable_returns_503(self, client: TestClient):
        with patch("app.deps.get_neo4j", return_value=None):
            resp = client.get("/wiki/entities/person:elon-musk/backlinks")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /wiki/entities/{slug}/refresh — WK4 manual refresh
# ---------------------------------------------------------------------------


class TestManualRefresh:
    def test_returns_202_on_success(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch("app.routers.wiki.get_entity_page", new=AsyncMock(return_value=_make_wiki_page())),
            patch("app.routers.wiki.enqueue_refresh", return_value=True),
        ):
            resp = client.post("/wiki/entities/person:elon-musk/refresh")
        assert resp.status_code == 202

    def test_enqueues_with_force_true(self, client: TestClient):
        captured: list[Any] = []

        def _mock_enqueue(slug: str, *, force: bool = False) -> bool:
            captured.append({"slug": slug, "force": force})
            return True

        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch("app.routers.wiki.get_entity_page", new=AsyncMock(return_value=_make_wiki_page())),
            patch("app.routers.wiki.enqueue_refresh", side_effect=_mock_enqueue),
        ):
            client.post("/wiki/entities/person:elon-musk/refresh")

        assert captured == [{"slug": "person:elon-musk", "force": True}]

    def test_returns_404_when_entity_missing(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch("app.routers.wiki.get_entity_page", new=AsyncMock(return_value=None)),
        ):
            resp = client.post("/wiki/entities/nonexistent:slug/refresh")
        assert resp.status_code == 404

    def test_response_body_shape(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch("app.routers.wiki.get_entity_page", new=AsyncMock(return_value=_make_wiki_page())),
            patch("app.routers.wiki.enqueue_refresh", return_value=True),
        ):
            resp = client.post("/wiki/entities/person:elon-musk/refresh")
        data = resp.json()
        assert "enqueued" in data
        assert data["enqueued"] is True


# ---------------------------------------------------------------------------
# PATCH /wiki/entities/{slug} — WK4 manual summary edit
# ---------------------------------------------------------------------------


class TestManualSummaryEdit:
    def test_returns_updated_page(self, client: TestClient):
        updated_page = _make_wiki_page()
        updated_page.summary = "User-edited summary."
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch("app.routers.wiki.get_entity_page", new=AsyncMock(side_effect=[
                _make_wiki_page(),  # existence check
                updated_page,       # after write
            ])),
            patch("app.routers.wiki.write_entity_summary"),
            patch("app.routers.wiki.append_log_entry"),
        ):
            resp = client.patch(
                "/wiki/entities/person:elon-musk",
                json={"summary": "User-edited summary."},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"] == "User-edited summary."

    def test_write_entity_summary_called_with_edited_by(self, client: TestClient):
        captured: list[Any] = []

        def _mock_write(driver: Any, slug: str, summary: str, summary_updated_at: str, *, edited_by: str = "") -> None:
            captured.append({"slug": slug, "edited_by": edited_by})

        updated_page = _make_wiki_page()
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch("app.routers.wiki.get_entity_page", new=AsyncMock(side_effect=[
                _make_wiki_page(),
                updated_page,
            ])),
            patch("app.routers.wiki.write_entity_summary", side_effect=_mock_write),
            patch("app.routers.wiki.append_log_entry"),
        ):
            client.patch(
                "/wiki/entities/person:elon-musk",
                json={"summary": "Edited."},
            )

        assert len(captured) == 1
        assert captured[0]["slug"] == "person:elon-musk"
        assert captured[0]["edited_by"] == "user"

    def test_append_log_entry_called_with_manual_edit(self, client: TestClient):
        captured: list[Any] = []

        def _mock_log(driver: Any, *, action: str, entity_slug: str | None, **kwargs: Any) -> str:
            captured.append({"action": action, "entity_slug": entity_slug})
            return "log-id"

        updated_page = _make_wiki_page()
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch("app.routers.wiki.get_entity_page", new=AsyncMock(side_effect=[
                _make_wiki_page(),
                updated_page,
            ])),
            patch("app.routers.wiki.write_entity_summary"),
            patch("app.routers.wiki.append_log_entry", side_effect=_mock_log),
        ):
            client.patch(
                "/wiki/entities/person:elon-musk",
                json={"summary": "Edited."},
            )

        assert len(captured) == 1
        assert captured[0]["action"] == "manual_edit"
        assert captured[0]["entity_slug"] == "person:elon-musk"

    def test_returns_404_when_entity_missing(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=MagicMock()),
            patch("app.routers.wiki.get_entity_page", new=AsyncMock(return_value=None)),
        ):
            resp = client.patch(
                "/wiki/entities/nonexistent:slug",
                json={"summary": "Hello."},
            )
        assert resp.status_code == 404
