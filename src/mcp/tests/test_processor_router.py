# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Unit tests for the /processor FastAPI router.

Uses FastAPI TestClient against a minimal app that mounts the processor
router.  The queue on ``app.state`` is a MagicMock; metrics are patched.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.processor.router import router
from core.processor.job import JobRecord, JobState
from core.processor.priority import Priority

# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


def _make_job_record(job_id: str | None = None) -> JobRecord:
    return JobRecord(
        id=job_id or str(uuid.uuid4()),
        job_type="stub_job",
        state=JobState.COMPLETED,
        priority=Priority.LOW,
        payload={"artifact_id": "a1"},
        enqueued_at=datetime.now(tz=timezone.utc),
    )


@pytest.fixture
def mock_queue() -> MagicMock:
    q = MagicMock()
    q.size_by_priority = AsyncMock(
        return_value={Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 3}
    )
    q.list_recent = AsyncMock(return_value=[_make_job_record("job-a"), _make_job_record("job-b")])
    q.pause = AsyncMock()
    q.resume = AsyncMock()
    # RedisJobQueue's own async paused-check (router.py calls this instead
    # of reaching into the private ``_r`` redis client directly).
    q._is_paused = AsyncMock(return_value=False)
    return q


@pytest.fixture
def test_app(mock_queue) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.processor_queue = mock_queue
    return app


@pytest.fixture
def client(test_app) -> TestClient:
    return TestClient(test_app)


# ---------------------------------------------------------------------------
# GET /processor/status
# ---------------------------------------------------------------------------


def test_processor_status_shape(client):
    """GET /processor/status returns the documented shape."""
    with (
        patch("app.processor.router.get_redis") as mock_get_redis,
        patch("app.processor.router.processor_jobs_completed_24h", new_callable=AsyncMock) as mock_jobs,
        patch("app.processor.router.processor_cost_usd_7d", new_callable=AsyncMock) as mock_cost,
        patch("app.processor.router.processor_throttled_ticks", new_callable=AsyncMock) as mock_throttled,
    ):
        mock_get_redis.return_value = MagicMock()
        mock_jobs.return_value = 7
        mock_cost.return_value = Decimal("0.42")
        mock_throttled.return_value = 2

        resp = client.get("/processor/status")

    assert resp.status_code == 200
    body = resp.json()
    assert "queue_sizes" in body
    assert "paused" in body
    assert "jobs_completed_24h" in body
    assert "cost_usd_7d" in body
    assert "throttled_ticks_1h" in body
    assert body["jobs_completed_24h"] == 7
    assert abs(body["cost_usd_7d"] - 0.42) < 1e-6
    assert body["throttled_ticks_1h"] == 2


def test_processor_status_reflects_paused_true(client, mock_queue):
    """GET /processor/status surfaces paused=True via queue._is_paused()."""
    mock_queue._is_paused = AsyncMock(return_value=True)
    with (
        patch("app.processor.router.get_redis") as mock_get_redis,
        patch("app.processor.router.processor_jobs_completed_24h", new_callable=AsyncMock) as mock_jobs,
        patch("app.processor.router.processor_cost_usd_7d", new_callable=AsyncMock) as mock_cost,
        patch("app.processor.router.processor_throttled_ticks", new_callable=AsyncMock) as mock_throttled,
    ):
        mock_get_redis.return_value = MagicMock()
        mock_jobs.return_value = 0
        mock_cost.return_value = Decimal("0")
        mock_throttled.return_value = 0

        resp = client.get("/processor/status")

    assert resp.status_code == 200
    assert resp.json()["paused"] is True


def test_processor_status_paused_probe_degrades_to_false_on_error(client, mock_queue):
    """A broken _is_paused() must not 500 the status endpoint."""
    mock_queue._is_paused = AsyncMock(side_effect=RuntimeError("queue unreachable"))
    with (
        patch("app.processor.router.get_redis") as mock_get_redis,
        patch("app.processor.router.processor_jobs_completed_24h", new_callable=AsyncMock) as mock_jobs,
        patch("app.processor.router.processor_cost_usd_7d", new_callable=AsyncMock) as mock_cost,
        patch("app.processor.router.processor_throttled_ticks", new_callable=AsyncMock) as mock_throttled,
    ):
        mock_get_redis.return_value = MagicMock()
        mock_jobs.return_value = 0
        mock_cost.return_value = Decimal("0")
        mock_throttled.return_value = 0

        resp = client.get("/processor/status")

    assert resp.status_code == 200
    assert resp.json()["paused"] is False


def test_processor_status_includes_job_type_latency(client):
    """GET /processor/status surfaces per-job-type p50/p95 latency (Phase 0.4a)."""
    with (
        patch("app.processor.router.get_redis") as mock_get_redis,
        patch("app.processor.router.processor_jobs_completed_24h", new_callable=AsyncMock) as mock_jobs,
        patch("app.processor.router.processor_cost_usd_7d", new_callable=AsyncMock) as mock_cost,
        patch("app.processor.router.processor_throttled_ticks", new_callable=AsyncMock) as mock_throttled,
        patch("app.processor.router.processor_job_type_stats", new_callable=AsyncMock) as mock_jt,
    ):
        mock_get_redis.return_value = MagicMock()
        mock_jobs.return_value = 0
        mock_cost.return_value = Decimal("0")
        mock_throttled.return_value = 0
        mock_jt.return_value = {"wiki_refresh": {"count": 5, "p50_s": 42.0, "p95_s": 98.5}}

        resp = client.get("/processor/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["job_type_latency"] == {
        "wiki_refresh": {"count": 5, "p50_s": 42.0, "p95_s": 98.5}
    }


def test_processor_status_job_type_latency_degrades_to_empty_on_error(client):
    """A broken job-type-stats reader must not 500 the status endpoint."""
    with (
        patch("app.processor.router.get_redis") as mock_get_redis,
        patch("app.processor.router.processor_jobs_completed_24h", new_callable=AsyncMock) as mock_jobs,
        patch("app.processor.router.processor_cost_usd_7d", new_callable=AsyncMock) as mock_cost,
        patch("app.processor.router.processor_throttled_ticks", new_callable=AsyncMock) as mock_throttled,
        patch("app.processor.router.processor_job_type_stats", new_callable=AsyncMock) as mock_jt,
    ):
        mock_get_redis.return_value = MagicMock()
        mock_jobs.return_value = 0
        mock_cost.return_value = Decimal("0")
        mock_throttled.return_value = 0
        mock_jt.side_effect = RuntimeError("redis down")

        resp = client.get("/processor/status")

    assert resp.status_code == 200
    assert resp.json()["job_type_latency"] == {}


def test_processor_status_includes_chat_telemetry(client):
    """GET /processor/status surfaces chat route counts + per-model latency (Phase 0.4a)."""
    with (
        patch("app.processor.router.get_redis") as mock_get_redis,
        patch("app.processor.router.processor_jobs_completed_24h", new_callable=AsyncMock) as mock_jobs,
        patch("app.processor.router.processor_cost_usd_7d", new_callable=AsyncMock) as mock_cost,
        patch("app.processor.router.processor_throttled_ticks", new_callable=AsyncMock) as mock_throttled,
        patch("app.processor.router.get_chat_route_counts_today") as mock_routes,
        patch("app.processor.router.get_chat_model_latency_stats") as mock_latency,
    ):
        mock_get_redis.return_value = MagicMock()
        mock_jobs.return_value = 0
        mock_cost.return_value = Decimal("0")
        mock_throttled.return_value = 0
        mock_routes.return_value = {"explicit": 3, "auto": 7, "fallback": 1}
        mock_latency.return_value = {
            "anthropic/claude-sonnet-4.6": {"count": 10, "p50_s": 4.2, "p95_s": 11.0}
        }

        resp = client.get("/processor/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["chat_route_counts_today"] == {"explicit": 3, "auto": 7, "fallback": 1}
    assert body["chat_model_latency"] == {
        "anthropic/claude-sonnet-4.6": {"count": 10, "p50_s": 4.2, "p95_s": 11.0}
    }


def test_processor_status_includes_mode_and_spend(client):
    """GET /processor/status surfaces mode, monthly spend, and cap (Task 2.5c)."""
    import config

    with (
        patch("app.processor.router.get_redis") as mock_get_redis,
        patch("app.processor.router.processor_jobs_completed_24h", new_callable=AsyncMock) as mock_jobs,
        patch("app.processor.router.processor_cost_usd_7d", new_callable=AsyncMock) as mock_cost,
        patch("app.processor.router.processor_throttled_ticks", new_callable=AsyncMock) as mock_throttled,
        patch("app.processor.router.processor_cost_usd_month", new_callable=AsyncMock) as mock_month,
    ):
        mock_get_redis.return_value = MagicMock()
        mock_jobs.return_value = 0
        mock_cost.return_value = Decimal("0")
        mock_throttled.return_value = 0
        mock_month.return_value = Decimal("1.23")

        saved_mode = config.settings.PROCESSOR_MODE
        saved_cap = config.settings.PROCESSOR_MONTHLY_CAP_USD
        config.settings.PROCESSOR_MODE = "hybrid"
        config.settings.PROCESSOR_MONTHLY_CAP_USD = 5.0
        try:
            resp = client.get("/processor/status")
        finally:
            config.settings.PROCESSOR_MODE = saved_mode
            config.settings.PROCESSOR_MONTHLY_CAP_USD = saved_cap

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "hybrid"
    assert abs(body["monthly_spend_usd"] - 1.23) < 1e-6
    assert body["cap_usd"] == 5.0


def test_processor_status_monthly_spend_degrades_to_zero_on_redis_error(client):
    """A redis error fetching monthly spend must not 500 the status endpoint."""
    with (
        patch("app.processor.router.get_redis") as mock_get_redis,
        patch("app.processor.router.processor_jobs_completed_24h", new_callable=AsyncMock) as mock_jobs,
        patch("app.processor.router.processor_cost_usd_7d", new_callable=AsyncMock) as mock_cost,
        patch("app.processor.router.processor_throttled_ticks", new_callable=AsyncMock) as mock_throttled,
        patch("app.processor.router.processor_cost_usd_month", new_callable=AsyncMock) as mock_month,
    ):
        mock_get_redis.return_value = MagicMock()
        mock_jobs.return_value = 0
        mock_cost.return_value = Decimal("0")
        mock_throttled.return_value = 0
        mock_month.side_effect = RuntimeError("redis down")

        resp = client.get("/processor/status")

    assert resp.status_code == 200
    assert resp.json()["monthly_spend_usd"] == 0.0


def test_processor_status_queue_sizes(client, mock_queue):
    """Queue sizes are present and keyed by priority value."""
    with (
        patch("app.processor.router.get_redis") as mock_get_redis,
        patch("app.processor.router.processor_jobs_completed_24h", new_callable=AsyncMock) as mock_jobs,
        patch("app.processor.router.processor_cost_usd_7d", new_callable=AsyncMock) as mock_cost,
        patch("app.processor.router.processor_throttled_ticks", new_callable=AsyncMock) as mock_throttled,
    ):
        mock_get_redis.return_value = MagicMock()
        mock_jobs.return_value = 0
        mock_cost.return_value = Decimal("0")
        mock_throttled.return_value = 0

        resp = client.get("/processor/status")

    body = resp.json()
    assert "high" in body["queue_sizes"]
    assert "medium" in body["queue_sizes"]
    assert "low" in body["queue_sizes"]
    assert body["queue_sizes"]["medium"] == 1
    assert body["queue_sizes"]["low"] == 3


def test_processor_status_no_queue(test_app):
    """GET /processor/status works even when processor_queue is absent."""
    # Remove the queue
    del test_app.state.processor_queue
    temp_client = TestClient(test_app)
    with (
        patch("app.processor.router.get_redis") as mock_get_redis,
        patch("app.processor.router.processor_jobs_completed_24h", new_callable=AsyncMock) as mock_jobs,
        patch("app.processor.router.processor_cost_usd_7d", new_callable=AsyncMock) as mock_cost,
        patch("app.processor.router.processor_throttled_ticks", new_callable=AsyncMock) as mock_throttled,
    ):
        mock_get_redis.return_value = MagicMock()
        mock_jobs.return_value = 0
        mock_cost.return_value = Decimal("0")
        mock_throttled.return_value = 0
        resp = temp_client.get("/processor/status")
    assert resp.status_code == 200
    # Restore for other tests
    test_app.state.processor_queue = MagicMock()


# ---------------------------------------------------------------------------
# GET /processor/recent
# ---------------------------------------------------------------------------


def test_processor_recent_returns_list(client):
    """GET /processor/recent returns a list of job dicts."""
    resp = client.get("/processor/recent?limit=5")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 2  # mock_queue returns 2 records
    # Verify structure of one record
    assert "id" in body[0]
    assert "job_type" in body[0]
    assert "state" in body[0]


def test_processor_recent_passes_limit(client, mock_queue):
    """GET /processor/recent passes limit to queue.list_recent."""
    client.get("/processor/recent?limit=7")
    mock_queue.list_recent.assert_awaited_once_with(7)


def test_processor_recent_no_queue(test_app):
    """Returns empty list when processor_queue is absent."""
    del test_app.state.processor_queue
    temp_client = TestClient(test_app)
    resp = temp_client.get("/processor/recent")
    assert resp.status_code == 200
    assert resp.json() == []
    # Restore
    test_app.state.processor_queue = MagicMock()


# ---------------------------------------------------------------------------
# POST /processor/pause
# ---------------------------------------------------------------------------


def test_processor_pause_calls_queue(client, mock_queue):
    """POST /processor/pause calls queue.pause and returns paused=true."""
    resp = client.post("/processor/pause")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"paused": True}
    mock_queue.pause.assert_awaited_once()


# ---------------------------------------------------------------------------
# POST /processor/resume
# ---------------------------------------------------------------------------


def test_processor_resume_calls_queue(client, mock_queue):
    """POST /processor/resume calls queue.resume and returns paused=false."""
    resp = client.post("/processor/resume")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"paused": False}
    mock_queue.resume.assert_awaited_once()
