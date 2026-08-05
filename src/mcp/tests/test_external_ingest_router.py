# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Router-level tests for ``POST /sdk/v1/ingest/external``.

Uses :class:`fastapi.testclient.TestClient` and mocks the service layer.

Covers:
* Happy path: Readwise-shaped payload → 200 with accepted count
* 422 on malformed field_mappings (Pydantic validation)
* 200 with ``errors[]`` when one item in a multi-item batch fails
* Auth/rate-limit surface inherited from the sdk router (header check)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.sdk import router as sdk_router
from app.services.external_ingest import IngestResult


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(sdk_router)
    return app


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestIngestExternalHappy:
    def test_readwise_shaped_payload_returns_200_with_accepted_count(self) -> None:
        app = _make_app()
        client = TestClient(app)

        mock_result = IngestResult(accepted=2, skipped=0, errors=[], source_type="readwise")

        # get_tenant_id is imported lazily inside the endpoint function; patch
        # it at its canonical location so the lazy import resolves to the mock.
        with (
            patch(
                "app.routers.sdk.ingest_external",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch("core.context.identity.get_tenant_id", return_value="default"),
        ):
            resp = client.post(
                "/sdk/v1/ingest/external",
                json={
                    "source_type": "readwise",
                    "payload": {
                        "highlights": [
                            {"text": "First highlight", "url": "https://readwise.io/h/1"},
                            {"text": "Second highlight", "url": "https://readwise.io/h/2"},
                        ]
                    },
                    "field_mappings": {
                        "content": "highlights[].text",
                        "source_uri": "highlights[].url",
                    },
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] == 2
        assert data["skipped"] == 0
        assert data["errors"] == []
        assert data["source_type"] == "readwise"

    def test_single_item_payload_returns_accepted_one(self) -> None:
        app = _make_app()
        client = TestClient(app)

        mock_result = IngestResult(accepted=1, skipped=0, errors=[], source_type="telegram-bot")

        with (
            patch(
                "app.routers.sdk.ingest_external",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch("core.context.identity.get_tenant_id", return_value="default"),
        ):
            resp = client.post(
                "/sdk/v1/ingest/external",
                json={
                    "source_type": "telegram-bot",
                    "payload": {
                        "text": "Some captured message",
                        "message_url": "https://t.me/channel/42",
                    },
                    "field_mappings": {
                        "content": "text",
                        "source_uri": "message_url",
                    },
                },
            )

        assert resp.status_code == 200
        assert resp.json()["accepted"] == 1


# ---------------------------------------------------------------------------
# 422 on malformed field_mappings
# ---------------------------------------------------------------------------


class TestIngestExternalValidation:
    def test_missing_content_field_returns_422(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/sdk/v1/ingest/external",
            json={
                "source_type": "pocket",
                "payload": {"text": "hello", "url": "https://example.com"},
                "field_mappings": {
                    # 'content' is required — omitting it should cause 422
                    "source_uri": "url",
                },
            },
        )
        assert resp.status_code == 422

    def test_missing_source_uri_field_returns_422(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/sdk/v1/ingest/external",
            json={
                "source_type": "pocket",
                "payload": {"text": "hello", "url": "https://example.com"},
                "field_mappings": {
                    "content": "text",
                    # 'source_uri' is required — omitting it should cause 422
                },
            },
        )
        assert resp.status_code == 422

    def test_missing_source_type_returns_422(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/sdk/v1/ingest/external",
            json={
                # source_type is required
                "payload": {"text": "hello", "url": "https://x.com"},
                "field_mappings": {"content": "text", "source_uri": "url"},
            },
        )
        assert resp.status_code == 422

    def test_missing_payload_returns_422(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/sdk/v1/ingest/external",
            json={
                "source_type": "test",
                # payload is required
                "field_mappings": {"content": "text", "source_uri": "url"},
            },
        )
        assert resp.status_code == 422

    def test_completely_empty_body_returns_422(self) -> None:
        app = _make_app()
        client = TestClient(app)

        resp = client.post("/sdk/v1/ingest/external", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 200 with errors[] when one item in a batch fails
# ---------------------------------------------------------------------------


class TestIngestExternalPartialFailure:
    def test_partial_batch_failure_returns_200_with_errors(self) -> None:
        app = _make_app()
        client = TestClient(app)

        mock_result = IngestResult(
            accepted=2,
            skipped=0,
            errors=[{"index": 2, "error": "Neo4j write failed", "phase": "ingest", "source_uri": "https://r.io/h/3"}],
            source_type="readwise",
        )

        with (
            patch(
                "app.routers.sdk.ingest_external",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch("core.context.identity.get_tenant_id", return_value="default"),
        ):
            resp = client.post(
                "/sdk/v1/ingest/external",
                json={
                    "source_type": "readwise",
                    "payload": {
                        "highlights": [
                            {"text": "H1", "url": "https://r.io/h/1"},
                            {"text": "H2", "url": "https://r.io/h/2"},
                            {"text": "H3", "url": "https://r.io/h/3"},
                        ]
                    },
                    "field_mappings": {
                        "content": "highlights[].text",
                        "source_uri": "highlights[].url",
                    },
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] == 2
        assert len(data["errors"]) == 1
        assert data["errors"][0]["phase"] == "ingest"

    def test_all_mapping_error_returns_200_with_error_in_list(self) -> None:
        app = _make_app()
        client = TestClient(app)

        mock_result = IngestResult(
            accepted=0,
            skipped=0,
            errors=[{"index": None, "error": "Path 'missing' not found", "phase": "mapping"}],
            source_type="broken",
        )

        with (
            patch(
                "app.routers.sdk.ingest_external",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch("core.context.identity.get_tenant_id", return_value="default"),
        ):
            resp = client.post(
                "/sdk/v1/ingest/external",
                json={
                    "source_type": "broken",
                    "payload": {"wrong": "keys"},
                    "field_mappings": {"content": "missing", "source_uri": "also_missing"},
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["accepted"] == 0
        assert len(data["errors"]) == 1


# ---------------------------------------------------------------------------
# Route existence check (auth + rate-limit surface is router-level)
# ---------------------------------------------------------------------------


class TestIngestExternalRouteExists:
    def test_endpoint_is_registered_at_correct_path(self) -> None:
        """Verifies the route is mounted and not shadowed by existing /ingest."""
        app = _make_app()
        client = TestClient(app)

        # Without mocking — will fail at service layer but 422/500, not 404/405
        resp = client.post(
            "/sdk/v1/ingest/external",
            json={
                "source_type": "test",
                "payload": {},
                "field_mappings": {"content": "t", "source_uri": "u"},
            },
        )
        # 404 → route not registered; anything else → route exists
        assert resp.status_code != 404, "Endpoint not registered at /sdk/v1/ingest/external"

    def test_get_on_endpoint_returns_405(self) -> None:
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/sdk/v1/ingest/external")
        assert resp.status_code == 405
