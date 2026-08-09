# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Router tests for POST /sdk/v1/feedback (Phase R.1).

Uses the FastAPI TestClient against the fully-wired app.  The Neo4j
adapter is mocked at the service boundary so no live database is needed.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_test_app() -> FastAPI:
    """Minimal FastAPI app with only the feedback router mounted.

    Avoids importing app.main (which drags in the full dependency graph
    including Redis, Neo4j, the processor worker, etc.) in unit tests.
    """
    from app.routers.feedback import router as feedback_router
    test_app = FastAPI()
    test_app.include_router(feedback_router)
    return test_app


@pytest.fixture()
def client() -> TestClient:
    """TestClient scoped to the feedback router only."""
    return TestClient(_make_test_app(), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestSubmitFeedbackEndpoint:
    def test_happy_path_positive_sentiment(self, client: TestClient) -> None:
        with patch(
            "app.routers.feedback.submit_feedback",
            new_callable=AsyncMock,
            return_value="rating-abc123",
        ):
            res = client.post(
                "/sdk/v1/feedback",
                json={"claim_id": "claim-001", "sentiment": 1, "session_id": "sess-xyz"},
            )
        assert res.status_code == 201
        body = res.json()
        assert body["ok"] is True
        assert body["rating_id"] == "rating-abc123"

    def test_happy_path_negative_sentiment(self, client: TestClient) -> None:
        with patch(
            "app.routers.feedback.submit_feedback",
            new_callable=AsyncMock,
            return_value="rating-def456",
        ):
            res = client.post(
                "/sdk/v1/feedback",
                json={"claim_id": "claim-002", "sentiment": -1, "user_id": "user-001"},
            )
        assert res.status_code == 201
        assert res.json()["ok"] is True

    def test_happy_path_neutral_sentiment(self, client: TestClient) -> None:
        with patch(
            "app.routers.feedback.submit_feedback",
            new_callable=AsyncMock,
            return_value="rating-neutral-789",
        ):
            res = client.post(
                "/sdk/v1/feedback",
                json={"claim_id": "claim-003", "sentiment": 0},
            )
        assert res.status_code == 201

    def test_with_comment(self, client: TestClient) -> None:
        with patch(
            "app.routers.feedback.submit_feedback",
            new_callable=AsyncMock,
            return_value="rating-with-comment",
        ):
            res = client.post(
                "/sdk/v1/feedback",
                json={
                    "claim_id": "claim-004",
                    "sentiment": 1,
                    "comment": "This claim is accurate.",
                    "session_id": "sess-abc",
                },
            )
        assert res.status_code == 201
        assert res.json()["rating_id"] == "rating-with-comment"


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class TestFeedbackValidationErrors:
    def test_missing_claim_id_returns_422(self, client: TestClient) -> None:
        res = client.post("/sdk/v1/feedback", json={"sentiment": 1})
        assert res.status_code == 422

    def test_invalid_sentiment_too_high_returns_422(self, client: TestClient) -> None:
        res = client.post(
            "/sdk/v1/feedback",
            json={"claim_id": "claim-001", "sentiment": 2},
        )
        assert res.status_code == 422

    def test_invalid_sentiment_too_low_returns_422(self, client: TestClient) -> None:
        res = client.post(
            "/sdk/v1/feedback",
            json={"claim_id": "claim-001", "sentiment": -2},
        )
        assert res.status_code == 422

    def test_comment_too_long_returns_422(self, client: TestClient) -> None:
        res = client.post(
            "/sdk/v1/feedback",
            json={"claim_id": "claim-001", "sentiment": 1, "comment": "x" * 501},
        )
        assert res.status_code == 422

    def test_empty_body_returns_422(self, client: TestClient) -> None:
        res = client.post("/sdk/v1/feedback", json={})
        assert res.status_code == 422


# ---------------------------------------------------------------------------
# Service error → 503
# ---------------------------------------------------------------------------


class TestFeedbackServiceError:
    def test_neo4j_error_returns_503(self, client: TestClient) -> None:
        with patch(
            "app.routers.feedback.submit_feedback",
            new_callable=AsyncMock,
            side_effect=RuntimeError("neo4j down"),
        ):
            res = client.post(
                "/sdk/v1/feedback",
                json={"claim_id": "claim-001", "sentiment": 1},
            )
        assert res.status_code == 503
        assert "retry" in res.json()["detail"].lower()
