# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the /ingest/structured endpoint (Phase D Day 5).

This endpoint is the integration point for the desktop Apple connectors
(Notes / Mail / Messages). The backend test here verifies the contract
shape; the connectors themselves are tested in the desktop package's
own vitest suite.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app() -> FastAPI:
    from app.routers.ingestion import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def client():
    return TestClient(_make_app())


class TestStructuredIngest:
    def test_accepts_minimal_payload(self, client):
        with patch(
            "app.routers.ingestion.ingest_content",
            return_value={"status": "ok", "artifact_id": "art:1"},
        ) as mock_ingest:
            resp = client.post(
                "/ingest/structured",
                json={"content": "hello world", "domain": "notes"},
            )
        assert resp.status_code == 200
        # ingest_content called with (content, domain, metadata)
        args, _kwargs = mock_ingest.call_args
        assert args[0] == "hello world"
        assert args[1] == "notes"
        # metadata is empty dict when neither metadata nor source_id is provided
        # (and the client header isn't set in TestClient default)
        assert args[2] == {}

    def test_forwards_metadata_to_ingest_content(self, client):
        with patch(
            "app.routers.ingestion.ingest_content",
            return_value={"status": "ok"},
        ) as mock_ingest:
            client.post(
                "/ingest/structured",
                json={
                    "content": "subject: foo\n\nbody",
                    "domain": "mail",
                    "metadata": {
                        "source": "apple_mail",
                        "account": "iCloud",
                        "from": "alice@example.com",
                    },
                },
            )
        args, _kwargs = mock_ingest.call_args
        meta = args[2]
        assert meta["source"] == "apple_mail"
        assert meta["account"] == "iCloud"
        assert meta["from"] == "alice@example.com"

    def test_source_id_promoted_into_metadata(self, client):
        with patch(
            "app.routers.ingestion.ingest_content",
            return_value={"status": "ok"},
        ) as mock_ingest:
            client.post(
                "/ingest/structured",
                json={
                    "content": "x",
                    "domain": "notes",
                    "source_id": "apple_notes:42",
                },
            )
        args, _kwargs = mock_ingest.call_args
        assert args[2]["source_id"] == "apple_notes:42"

    def test_x_client_id_header_lands_in_metadata(self, client):
        with patch(
            "app.routers.ingestion.ingest_content",
            return_value={"status": "ok"},
        ) as mock_ingest:
            client.post(
                "/ingest/structured",
                json={"content": "x", "domain": "messages"},
                headers={"X-Client-ID": "imessage"},
            )
        args, _kwargs = mock_ingest.call_args
        assert args[2]["client_source"] == "imessage"

    def test_metadata_must_be_str_to_str(self, client):
        """The endpoint declares dict[str, str]. Numeric values reject."""
        resp = client.post(
            "/ingest/structured",
            json={
                "content": "x",
                "domain": "mail",
                "metadata": {"count": 42},  # not a string
            },
        )
        assert resp.status_code == 422

    def test_default_domain_when_omitted(self, client):
        with patch(
            "app.routers.ingestion.ingest_content",
            return_value={"status": "ok"},
        ) as mock_ingest:
            client.post(
                "/ingest/structured",
                json={"content": "hello"},
            )
        args, _kwargs = mock_ingest.call_args
        assert args[1] == "general"
