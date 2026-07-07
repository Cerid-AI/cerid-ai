# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the /ingest/url endpoint (Task 2.3a).

Real URL capture for the quick-capture URL tab: fetch through the
SSRF-guarded fetcher, extract title + text, and ingest as one artifact.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_HTML = (
    "<html><head><title>Doc Title</title></head>"
    "<body><h1>Page Heading</h1><p>Hello world paragraph.</p></body></html>"
)


def _fake_response(
    body: str,
    *,
    url: str = "https://example.com/article",
    status_code: int = 200,
    content_type: str = "text/html; charset=utf-8",
) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        headers={"content-type": content_type},
        content=body.encode("utf-8"),
        request=httpx.Request("GET", url),
    )


def _make_app() -> FastAPI:
    from app.routers.ingestion import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def client():
    return TestClient(_make_app())


class TestIngestUrl:
    def test_happy_path_html_extracts_and_ingests(self, client):
        with (
            patch("app.routers.ingestion.guarded_get") as mock_guarded_get,
            patch("app.routers.ingestion.ingest_content") as mock_ingest,
        ):
            mock_guarded_get.return_value = _fake_response(_HTML)
            mock_ingest.return_value = {"status": "success", "artifact_id": "art:1"}

            resp = client.post(
                "/ingest/url", json={"url": "https://example.com/article"},
            )

        assert resp.status_code == 200
        assert resp.json() == {"status": "success", "artifact_id": "art:1"}

        args, kwargs = mock_ingest.call_args
        content, domain = args[0], args[1]
        metadata = args[2]
        assert "Hello world paragraph." in content
        assert domain == "general"
        assert metadata["source_type"] == "url"
        assert metadata["url"] == "https://example.com/article"
        assert metadata["title"]  # non-empty, extracted title
        assert kwargs.get("enrich") is True

    def test_ssrf_blocked_returns_422_clean_message(self, client):
        with (
            patch("app.routers.ingestion.guarded_get") as mock_guarded_get,
            patch("app.routers.ingestion.ingest_content") as mock_ingest,
        ):
            mock_guarded_get.side_effect = ValueError(
                "refusing internal/private address(es) ['169.254.169.254'] "
                "for 'metadata.internal' (SSRF guard)"
            )

            resp = client.post(
                "/ingest/url",
                json={"url": "http://169.254.169.254/latest/meta-data"},
            )

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "not fetchable" in detail.lower()
        assert "Traceback" not in detail
        mock_ingest.assert_not_called()

    def test_non_html_content_type_passes_through(self, client):
        with (
            patch("app.routers.ingestion.guarded_get") as mock_guarded_get,
            patch("app.routers.ingestion.ingest_content") as mock_ingest,
        ):
            mock_guarded_get.return_value = _fake_response(
                "Plain text body content.",
                url="https://example.com/notes.txt",
                content_type="text/plain; charset=utf-8",
            )
            mock_ingest.return_value = {"status": "success"}

            resp = client.post(
                "/ingest/url", json={"url": "https://example.com/notes.txt"},
            )

        assert resp.status_code == 200
        args, _kwargs = mock_ingest.call_args
        assert args[0].strip() == "Plain text body content."

    def test_empty_extraction_returns_422(self, client):
        with (
            patch("app.routers.ingestion.guarded_get") as mock_guarded_get,
            patch("app.routers.ingestion.ingest_content") as mock_ingest,
        ):
            mock_guarded_get.return_value = _fake_response(
                "   ", url="https://example.com/empty", content_type="text/plain",
            )

            resp = client.post(
                "/ingest/url", json={"url": "https://example.com/empty"},
            )

        assert resp.status_code == 422
        assert "no extractable text" in resp.json()["detail"].lower()
        mock_ingest.assert_not_called()

    def test_network_error_returns_502(self, client):
        with (
            patch("app.routers.ingestion.guarded_get") as mock_guarded_get,
            patch("app.routers.ingestion.ingest_content") as mock_ingest,
        ):
            mock_guarded_get.side_effect = httpx.ConnectError("connection refused")

            resp = client.post(
                "/ingest/url", json={"url": "https://example.com/down"},
            )

        assert resp.status_code == 502
        assert "could not fetch" in resp.json()["detail"].lower()
        mock_ingest.assert_not_called()

    def test_non_2xx_status_returns_502(self, client):
        with (
            patch("app.routers.ingestion.guarded_get") as mock_guarded_get,
            patch("app.routers.ingestion.ingest_content") as mock_ingest,
        ):
            mock_guarded_get.return_value = _fake_response(
                "Not Found", url="https://example.com/missing",
                status_code=404, content_type="text/plain",
            )

            resp = client.post(
                "/ingest/url", json={"url": "https://example.com/missing"},
            )

        assert resp.status_code == 502
        mock_ingest.assert_not_called()

    def test_tags_thread_into_metadata_as_tags_json(self, client):
        with (
            patch("app.routers.ingestion.guarded_get") as mock_guarded_get,
            patch("app.routers.ingestion.ingest_content") as mock_ingest,
        ):
            mock_guarded_get.return_value = _fake_response(_HTML)
            mock_ingest.return_value = {"status": "success"}

            resp = client.post(
                "/ingest/url",
                json={
                    "url": "https://example.com/article",
                    "tags": ["Research", " ai "],
                },
            )

        assert resp.status_code == 200
        args, _kwargs = mock_ingest.call_args
        metadata = args[2]
        assert json.loads(metadata["tags_json"]) == ["research", "ai"]

    def test_custom_domain_forwarded(self, client):
        with (
            patch("app.routers.ingestion.guarded_get") as mock_guarded_get,
            patch("app.routers.ingestion.ingest_content") as mock_ingest,
        ):
            mock_guarded_get.return_value = _fake_response(_HTML)
            mock_ingest.return_value = {"status": "success"}

            client.post(
                "/ingest/url",
                json={"url": "https://example.com/article", "domain": "research"},
            )

        args, _kwargs = mock_ingest.call_args
        assert args[1] == "research"

    def test_happy_path_invalidates_query_cache(self, client):
        """Same contract as the other content-producing endpoints in this
        router (ingest_endpoint, ingest_structured_endpoint): a successful
        ingest schedules a best-effort query-cache invalidation."""
        with (
            patch("app.routers.ingestion.guarded_get") as mock_guarded_get,
            patch("app.routers.ingestion.ingest_content") as mock_ingest,
            patch(
                "utils.query_cache.invalidate_cache_non_blocking",
                new_callable=AsyncMock,
            ) as mock_invalidate,
        ):
            mock_guarded_get.return_value = _fake_response(_HTML)
            mock_ingest.return_value = {"status": "success", "artifact_id": "art:1"}

            resp = client.post(
                "/ingest/url", json={"url": "https://example.com/article"},
            )

        assert resp.status_code == 200
        mock_invalidate.assert_called_once()
