# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Integration tests for the community explorer HTTP routes (Phase R.2).

Uses FastAPI TestClient.  Neo4j is mocked at the service layer so no
live database is required.

Coverage:
- GET /observability/communities — list endpoint
- GET /observability/communities — min_size and limit query params
- GET /observability/communities/{id} — happy path
- GET /observability/communities/{id} — 404 on missing community
- GET /observability/communities — 503 when Neo4j is unavailable
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.neo4j.communities import (
    CommunityFull,
    CommunitySummary,
    MemberEntity,
    RelatedCommunity,
)
from app.routers.observability import router

# ---------------------------------------------------------------------------
# Minimal test app — only the observability router
# ---------------------------------------------------------------------------

_test_app = FastAPI()
_test_app.include_router(router)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_test_app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_summary(community_id: str = "0:7", member_count: int = 25) -> CommunitySummary:
    return CommunitySummary(
        community_id=community_id,
        level=0,
        summary="Machine learning research community centred on transformers.",
        member_count=member_count,
        last_summarized_at="2026-05-10T02:00:00+00:00",
    )


def _make_full(community_id: str = "0:7") -> CommunityFull:
    return CommunityFull(
        community_id=community_id,
        level=0,
        summary="Machine learning research community centred on transformers.",
        member_count=25,
        last_summarized_at="2026-05-10T02:00:00+00:00",
        members=[
            MemberEntity(
                canonical_id="person:yann-lecun",
                name="Yann LeCun",
                entity_type="PERSON",
            ),
        ],
        related_communities=[
            RelatedCommunity(community_id="0:4", co_mention_count=38),
        ],
    )


# ---------------------------------------------------------------------------
# GET /observability/communities
# ---------------------------------------------------------------------------


class TestListCommunitiesRoute:
    def test_returns_200_with_list(self, client: TestClient):
        summaries = [_make_summary("0:7"), _make_summary("0:3", member_count=12)]
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch(
                "app.services.community_pages.list_communities",
                return_value=summaries,
            ),
        ):
            resp = client.get("/observability/communities")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["community_id"] == "0:7"
        assert data[0]["member_count"] == 25

    def test_respects_min_size_param(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch(
                "app.services.community_pages.list_communities",
                return_value=[],
            ) as mock_fn,
        ):
            resp = client.get("/observability/communities?min_size=5&limit=10")

        assert resp.status_code == 200
        # Adapter receives the forwarded params
        mock_fn.assert_called_once()
        _, kwargs = mock_fn.call_args
        assert kwargs["min_size"] == 5
        assert kwargs["limit"] == 10

    def test_empty_list_returns_200(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch(
                "app.services.community_pages.list_communities",
                return_value=[],
            ),
        ):
            resp = client.get("/observability/communities")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_503_when_neo4j_unavailable(self, client: TestClient):
        with patch(
            "app.deps.get_neo4j",
            side_effect=RuntimeError("Neo4j connection refused"),
        ):
            resp = client.get("/observability/communities")

        assert resp.status_code == 503

    def test_response_shape_has_required_fields(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch(
                "app.services.community_pages.list_communities",
                return_value=[_make_summary()],
            ),
        ):
            resp = client.get("/observability/communities")

        row = resp.json()[0]
        assert "community_id" in row
        assert "level" in row
        assert "summary" in row
        assert "member_count" in row
        assert "last_summarized_at" in row


# ---------------------------------------------------------------------------
# GET /observability/communities/{community_id}
# ---------------------------------------------------------------------------


class TestGetCommunityRoute:
    def test_happy_path_returns_200(self, client: TestClient):
        full = _make_full("0:7")
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch(
                "app.services.community_pages.get_community",
                return_value=full,
            ),
        ):
            resp = client.get("/observability/communities/0:7")

        assert resp.status_code == 200
        data = resp.json()
        assert data["community_id"] == "0:7"
        assert data["member_count"] == 25
        assert len(data["members"]) == 1
        assert data["members"][0]["canonical_id"] == "person:yann-lecun"
        assert len(data["related_communities"]) == 1

    def test_404_on_missing_community(self, client: TestClient):
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch(
                "app.services.community_pages.get_community",
                return_value=None,
            ),
        ):
            resp = client.get("/observability/communities/0:999")

        assert resp.status_code == 404

    def test_503_when_neo4j_unavailable(self, client: TestClient):
        with patch(
            "app.deps.get_neo4j",
            side_effect=RuntimeError("Neo4j down"),
        ):
            resp = client.get("/observability/communities/0:7")

        assert resp.status_code == 503

    def test_detail_shape_has_members_and_related(self, client: TestClient):
        full = _make_full()
        with (
            patch("app.deps.get_neo4j", return_value=object()),
            patch(
                "app.services.community_pages.get_community",
                return_value=full,
            ),
        ):
            resp = client.get("/observability/communities/0:7")

        data = resp.json()
        assert "members" in data
        assert "related_communities" in data
        assert isinstance(data["members"], list)
        assert isinstance(data["related_communities"], list)
