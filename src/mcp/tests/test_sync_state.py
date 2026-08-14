# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the shared per-connector sync/ingest state (sf-1 status truth).

One backend state source consumed by /ingestion/progress, the web Sources
hero, the desktop connector cards, and the tray. These tests pin:

* the client report → derived-state contract (window open/reset, absolutes),
* the server-observed ingest counters (what the server DID, not what a
  client claims),
* the running-vs-stalled distinction UX-24 diagnosed,
* the router surface (report validation, list, progress inlining, and the
  X-Client-ID hook on the structured-ingest path).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.sync_state import (
    SYNC_ACTIVE_WINDOW_S,
    get_all_sync_states,
    get_sync_state,
    record_ingest_outcome,
    report_sync,
)


@pytest.fixture
def redis_client():
    server = fakeredis.FakeServer()
    return fakeredis.FakeRedis(server=server, decode_responses=True)


def _iso_ago(seconds: float) -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(seconds=seconds)).isoformat()


# ---------------------------------------------------------------------------
# Service — window lifecycle
# ---------------------------------------------------------------------------


class TestReportSync:
    def test_syncing_report_opens_a_window(self, redis_client):
        state = report_sync(redis_client, "apple_mail", phase="syncing", total=500)
        assert state is not None
        assert state["state"] == "syncing"
        assert state["total"] == 500
        assert state["window_started_at"] is not None

    def test_new_window_resets_counters_and_snapshots_ingested(self, redis_client):
        for _ in range(3):
            record_ingest_outcome(redis_client, "apple_mail", "success")
        report_sync(redis_client, "apple_mail", phase="syncing", total=10)
        state = get_sync_state(redis_client, "apple_mail")
        assert state["scanned"] == 0
        assert state["posted"] == 0
        # lifetime count survives; the window starts at zero
        assert state["ingested_total"] == 3
        assert state["window_ingested"] == 0

    def test_progress_reports_are_absolute_not_incremental(self, redis_client):
        report_sync(redis_client, "apple_mail", phase="syncing", total=100)
        report_sync(redis_client, "apple_mail", phase="syncing", scanned=40, posted=25)
        # replayed report (at-least-once delivery) cannot double-count
        report_sync(redis_client, "apple_mail", phase="syncing", scanned=40, posted=25)
        state = get_sync_state(redis_client, "apple_mail")
        assert state["scanned"] == 40
        assert state["posted"] == 25
        assert state["total"] == 100

    def test_mid_sync_report_does_not_reopen_the_window(self, redis_client):
        report_sync(redis_client, "apple_mail", phase="syncing", total=100)
        first = get_sync_state(redis_client, "apple_mail")["window_started_at"]
        report_sync(redis_client, "apple_mail", phase="syncing", scanned=10)
        assert get_sync_state(redis_client, "apple_mail")["window_started_at"] == first

    def test_idle_report_closes_the_window(self, redis_client):
        report_sync(redis_client, "apple_mail", phase="syncing", total=10)
        state = report_sync(redis_client, "apple_mail", phase="idle")
        assert state["state"] == "idle"

    def test_error_report_surfaces_error_state(self, redis_client):
        state = report_sync(
            redis_client, "apple_mail", phase="error", error="Full Disk Access denied",
        )
        assert state["state"] == "error"
        assert "Full Disk Access" in state["last_error"]

    def test_invalid_phase_raises(self, redis_client):
        with pytest.raises(ValueError):
            report_sync(redis_client, "apple_mail", phase="warp")

    def test_redis_failure_returns_none_not_raise(self):
        assert report_sync(None, "apple_mail", phase="syncing") is None


# ---------------------------------------------------------------------------
# Service — server-observed counters
# ---------------------------------------------------------------------------


class TestRecordIngestOutcome:
    def test_outcomes_route_to_the_right_counter(self, redis_client):
        record_ingest_outcome(redis_client, "apple_mail", "success")
        record_ingest_outcome(redis_client, "apple_mail", "updated")
        record_ingest_outcome(redis_client, "apple_mail", "duplicate")
        record_ingest_outcome(redis_client, "apple_mail", "error")
        state = get_sync_state(redis_client, "apple_mail")
        assert state["ingested_total"] == 2
        assert state["deduped_total"] == 1
        assert state["errored_total"] == 1

    def test_empty_connector_is_a_noop(self, redis_client):
        record_ingest_outcome(redis_client, "", "success")
        assert get_all_sync_states(redis_client) == []

    def test_window_ingested_tracks_the_current_window_only(self, redis_client):
        record_ingest_outcome(redis_client, "apple_mail", "success")
        report_sync(redis_client, "apple_mail", phase="syncing", total=10)
        record_ingest_outcome(redis_client, "apple_mail", "success")
        record_ingest_outcome(redis_client, "apple_mail", "success")
        state = get_sync_state(redis_client, "apple_mail")
        assert state["ingested_total"] == 3
        assert state["window_ingested"] == 2


# ---------------------------------------------------------------------------
# Service — derived running/stalled/ingesting states (UX-24)
# ---------------------------------------------------------------------------


class TestDerivedState:
    def test_silent_syncing_client_reads_as_stalled(self, redis_client):
        report_sync(redis_client, "apple_mail", phase="syncing", total=10)
        redis_client.hset(
            "cerid:sync:state:apple_mail",
            "updated_at",
            _iso_ago(SYNC_ACTIVE_WINDOW_S + 30),
        )
        state = get_sync_state(redis_client, "apple_mail")
        assert state["state"] == "stalled"

    def test_server_side_ingest_keeps_a_silent_sync_alive(self, redis_client):
        """The client stopped reporting but artifacts are still landing —
        that is a live sync, not a stall."""
        report_sync(redis_client, "apple_mail", phase="syncing", total=10)
        redis_client.hset(
            "cerid:sync:state:apple_mail",
            "updated_at",
            _iso_ago(SYNC_ACTIVE_WINDOW_S + 30),
        )
        record_ingest_outcome(redis_client, "apple_mail", "success")
        # record_ingest_outcome refreshes updated_at; age only last_ingest_at
        state = get_sync_state(redis_client, "apple_mail")
        assert state["state"] == "syncing"

    def test_pollers_without_a_window_read_as_ingesting(self, redis_client):
        record_ingest_outcome(redis_client, "email_imap", "success")
        state = get_sync_state(redis_client, "email_imap")
        assert state["state"] == "ingesting"

    def test_old_activity_reads_as_idle(self, redis_client):
        record_ingest_outcome(redis_client, "email_imap", "success")
        redis_client.hset(
            "cerid:sync:state:email_imap",
            mapping={
                "updated_at": _iso_ago(SYNC_ACTIVE_WINDOW_S + 30),
                "last_ingest_at": _iso_ago(SYNC_ACTIVE_WINDOW_S + 30),
            },
        )
        assert get_sync_state(redis_client, "email_imap")["state"] == "idle"

    def test_rate_and_eta_derive_from_the_window(self, redis_client):
        report_sync(redis_client, "apple_mail", phase="syncing", total=120)
        key = "cerid:sync:state:apple_mail"
        redis_client.hset(key, "window_started_at", _iso_ago(60))
        for _ in range(20):
            record_ingest_outcome(redis_client, "apple_mail", "success")
        report_sync(redis_client, "apple_mail", phase="syncing", scanned=30, posted=25)
        state = get_sync_state(redis_client, "apple_mail")
        # 20 ingested over ~60s ≈ 20/min
        assert state["rate_per_min"] == pytest.approx(20.0, rel=0.15)
        # 95 left at ~20/min ≈ 285s
        assert state["eta_seconds"] == pytest.approx(285, rel=0.2)

    def test_get_all_lists_every_connector(self, redis_client):
        report_sync(redis_client, "apple_mail", phase="syncing", total=10)
        record_ingest_outcome(redis_client, "apple_notes", "success")
        names = {s["connector"] for s in get_all_sync_states(redis_client)}
        assert names == {"apple_mail", "apple_notes"}

    def test_get_all_degrades_to_empty_on_redis_failure(self):
        assert get_all_sync_states(None) == []


# ---------------------------------------------------------------------------
# Router surface
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    from app.routers.ingestion import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(redis_client):
    with patch("app.routers.ingestion.get_redis", return_value=redis_client):
        yield TestClient(_make_app())


class TestSyncStateRoutes:
    def test_report_then_list_roundtrip(self, client):
        resp = client.post(
            "/ingestion/sync-state/apple_mail",
            json={"phase": "syncing", "total": 200, "scanned": 50, "posted": 40},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "syncing"
        assert body["total"] == 200

        listing = client.get("/ingestion/sync-state")
        assert listing.status_code == 200
        assert listing.json()["connectors"][0]["connector"] == "apple_mail"

    def test_invalid_connector_name_is_rejected(self, client):
        resp = client.post(
            "/ingestion/sync-state/Not%20A%20Slug!",
            json={"phase": "syncing"},
        )
        assert resp.status_code == 422

    def test_invalid_phase_is_rejected(self, client):
        resp = client.post(
            "/ingestion/sync-state/apple_mail",
            json={"phase": "warp"},
        )
        assert resp.status_code == 422

    def test_progress_endpoint_inlines_connector_state(self, client):
        client.post(
            "/ingestion/sync-state/apple_mail",
            json={"phase": "syncing", "total": 10},
        )
        resp = client.get("/ingestion/progress")
        assert resp.status_code == 200
        body = resp.json()
        assert body["files"] == []
        assert body["connectors"][0]["connector"] == "apple_mail"
        assert body["connectors"][0]["state"] == "syncing"

    def test_structured_ingest_counts_onto_shared_state(self, client, redis_client):
        # apple_mail is behind the AF-043 soft Pro gate — run entitled so the
        # request reaches the counting hook.
        with patch(
            "config.features.is_feature_enabled", return_value=True,
        ), patch(
            "app.routers.ingestion.ingest_content",
            return_value={"status": "success", "artifact_id": "art:1"},
        ):
            resp = client.post(
                "/ingest/structured",
                json={"content": "hello", "domain": "mail"},
                headers={"X-Client-ID": "apple_mail"},
            )
        assert resp.status_code == 200
        state = get_sync_state(redis_client, "apple_mail")
        assert state is not None
        assert state["ingested_total"] == 1

    def test_structured_ingest_without_client_id_counts_nothing(self, client, redis_client):
        with patch(
            "app.routers.ingestion.ingest_content",
            return_value={"status": "success"},
        ):
            client.post("/ingest/structured", json={"content": "hello", "domain": "mail"})
        assert get_all_sync_states(redis_client) == []

    def test_duplicate_ingest_counts_as_deduped_not_ingested(self, client, redis_client):
        with patch(
            "config.features.is_feature_enabled", return_value=True,
        ), patch(
            "app.routers.ingestion.ingest_content",
            return_value={"status": "duplicate"},
        ):
            client.post(
                "/ingest/structured",
                json={"content": "hello", "domain": "mail"},
                headers={"X-Client-ID": "apple_mail"},
            )
        state = get_sync_state(redis_client, "apple_mail")
        assert state["ingested_total"] == 0
        assert state["deduped_total"] == 1
