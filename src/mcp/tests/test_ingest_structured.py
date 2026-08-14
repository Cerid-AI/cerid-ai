# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch(
                "app.routers.ingestion.ingest_content",
                return_value={"status": "ok"},
            ) as mock_ingest,
        ):
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

    def test_non_uuid_source_id_reclassified_as_external_id(self, client):
        """AF-007/AF-052 — a non-UUID source_id (a connector's external id) must
        be routed to external_id, NOT source_id, so it can never reach the
        per-source quality floor as if it were a :Source UUID."""
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch(
                "app.routers.ingestion.ingest_content",
                return_value={"status": "ok"},
            ) as mock_ingest,
        ):
            client.post(
                "/ingest/structured",
                json={
                    "content": "x",
                    "domain": "notes",
                    "source_id": "apple_notes:42",
                },
                headers={"X-Client-ID": "apple_notes"},
            )
        meta = mock_ingest.call_args.args[2]
        assert meta.get("external_id") == "apple_notes:42"
        assert "source_id" not in meta  # never lands in the :Source slot
        # source_kind falls back to the X-Client-ID header
        assert meta.get("source_kind") == "apple_notes"

    def test_uuid_source_id_stays_in_source_id(self, client):
        """A genuine :Source UUID must still land in source_id so it reaches
        source-linking and the per-source quality floor."""
        source_uuid = "550e8400-e29b-41d4-a716-446655440000"
        with patch(
            "app.routers.ingestion.ingest_content",
            return_value={"status": "ok"},
        ) as mock_ingest:
            client.post(
                "/ingest/structured",
                json={
                    "content": "x",
                    "domain": "notes",
                    "source_id": source_uuid,
                },
            )
        meta = mock_ingest.call_args.args[2]
        assert meta.get("source_id") == source_uuid
        assert "external_id" not in meta

    def test_explicit_external_id_wins_over_uuid_source_id(self, client):
        """An explicit external_id is always external; a UUID source_id
        alongside it still resolves to the :Source slot."""
        source_uuid = "550e8400-e29b-41d4-a716-446655440000"
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch(
                "app.routers.ingestion.ingest_content",
                return_value={"status": "ok"},
            ) as mock_ingest,
        ):
            client.post(
                "/ingest/structured",
                json={
                    "content": "x",
                    "domain": "notes",
                    "external_id": "note-123",
                    "source_id": source_uuid,
                    "source_kind": "apple_notes",
                },
            )
        meta = mock_ingest.call_args.args[2]
        assert meta.get("external_id") == "note-123"
        assert meta.get("source_kind") == "apple_notes"
        assert meta.get("source_id") == source_uuid

    def test_x_client_id_header_lands_in_metadata(self, client):
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch(
                "app.routers.ingestion.ingest_content",
                return_value={"status": "ok"},
            ) as mock_ingest,
        ):
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


class TestStructuredIngestEntitlementGate:
    """AF-043 — /ingest/structured has zero tier checks, so a Community
    desktop user could ingest Apple Notes/Mail/iMessage content for free.
    Gate only the Pro Apple connector source_kinds; everything else stays
    open (see ``_CONNECTOR_FEATURE_BY_SOURCE_KIND``)."""

    def test_community_tier_apple_notes_returns_402(self, client):
        with (
            patch("config.features.is_feature_enabled", return_value=False),
            patch("app.routers.ingestion.ingest_content") as mock_ingest,
        ):
            resp = client.post(
                "/ingest/structured",
                json={"content": "note body", "domain": "notes"},
                headers={"X-Client-ID": "apple_notes"},
            )
        assert resp.status_code == 402
        mock_ingest.assert_not_called()

    def test_pro_tier_apple_notes_returns_200(self, client):
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch(
                "app.routers.ingestion.ingest_content",
                return_value={"status": "ok"},
            ) as mock_ingest,
        ):
            resp = client.post(
                "/ingest/structured",
                json={"content": "note body", "domain": "notes"},
                headers={"X-Client-ID": "apple_notes"},
            )
        assert resp.status_code == 200
        mock_ingest.assert_called_once()

    def test_community_tier_generic_payload_stays_open(self, client):
        """A non-connector caller (generic API/tooling, no X-Client-ID match
        in the gated map) is untouched by the entitlement check."""
        with (
            patch("config.features.is_feature_enabled", return_value=False),
            patch(
                "app.routers.ingestion.ingest_content",
                return_value={"status": "ok"},
            ) as mock_ingest,
        ):
            resp = client.post(
                "/ingest/structured",
                json={"content": "hello", "domain": "general"},
            )
        assert resp.status_code == 200
        mock_ingest.assert_called_once()

    def test_gate_checks_the_correct_feature_flag_per_connector(self, client):
        cases = [
            ("apple_notes", "apple_notes_reader"),
            ("apple_mail", "apple_mail_reader"),
            ("imessage", "imessage_reader"),
        ]
        for client_id, expected_flag in cases:
            with patch(
                "config.features.is_feature_enabled", return_value=False
            ) as mock_enabled:
                resp = client.post(
                    "/ingest/structured",
                    json={"content": "x", "domain": "notes"},
                    headers={"X-Client-ID": client_id},
                )
            assert resp.status_code == 402, client_id
            mock_enabled.assert_called_once_with(expected_flag)
