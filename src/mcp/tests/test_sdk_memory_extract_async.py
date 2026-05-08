# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contract tests for the async memory_extract path (Workstream A
interface issue A — the systemic close-out).

Class of problem: post-fact annotation work shouldn't tie up request
slots. ``/sdk/v1/memory/extract`` synchronously fans out to N memories
× (consolidation + conflict-resolution) LLM calls + Neo4j/Chroma
writes; the per-stage budgets shipped in Phase 1.2 cap individual LLM
calls but the loops still multiply. Worst case is 60 s+, which keeps
a request slot occupied that whole time.

Systemic shape: convert to async/202 with a status endpoint. The
synchronous escape hatch (``?wait=true``) is preserved for callers
that need the result envelope inline. The default (when
``MEMORY_QUEUE_MODE=async``) is fire-and-forget: 202 + ``job_id``,
poll ``/sdk/v1/memory/extract/jobs/{job_id}`` for the result.

These tests run in the default ``test`` job — they mock the rq
``Queue.enqueue`` call so no live worker is required. The integration
between the worker and the live cerid-memory queue is exercised in
``tests/integration/`` (live-stack tier), not here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app_client():
    from app.routers import sdk

    app = FastAPI()
    app.include_router(sdk.router)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Default-async behaviour — POST returns 202 when MEMORY_QUEUE_MODE=async
# ---------------------------------------------------------------------------


class TestMemoryExtractAsync:
    def test_async_mode_returns_202_with_job_id(self, app_client):
        """``MEMORY_QUEUE_MODE=async`` + no ``?wait=true`` → 202 with
        ``job_id``, ``status='queued'``, ``status_url`` pointing at the
        job-status endpoint, and a ``Location`` header for HTTP-spec
        compliance."""
        fake_job = MagicMock()
        fake_job.id = "abc-123-job-id"
        fake_queue = MagicMock()
        fake_queue.enqueue.return_value = fake_job

        with (
            patch("app.queue.is_memory_async_mode", return_value=True),
            patch("app.queue.get_memory_queue", return_value=fake_queue),
        ):
            res = app_client.post(
                "/sdk/v1/memory/extract",
                json={
                    "response_text": "x" * 300,
                    "conversation_id": "async-test",
                },
            )
        assert res.status_code == 202, res.text
        assert res.headers.get("Location") == "/sdk/v1/memory/extract/jobs/abc-123-job-id"
        body = res.json()
        assert body["job_id"] == "abc-123-job-id"
        assert body["status"] == "queued"
        assert body["status_url"] == "/sdk/v1/memory/extract/jobs/abc-123-job-id"
        assert body["conversation_id"] == "async-test"
        # And the queue actually got the right kwargs
        assert fake_queue.enqueue.call_args.args == ("app.queue.tasks.memory_extract_task",)
        kwargs = fake_queue.enqueue.call_args.kwargs["kwargs"]
        assert kwargs["response_text"] == "x" * 300
        assert kwargs["conversation_id"] == "async-test"

    def test_wait_true_forces_sync_path(self, app_client):
        """``?wait=true`` always uses the synchronous path even when the
        queue is enabled. Lets callers that need the result inline opt
        out of the async default."""
        sync_result = {
            "conversation_id": "wait-test",
            "timestamp": "2026-05-08T00:00:00Z",
            "memories_extracted": 0,
            "memories_stored": 0,
            "skipped_duplicates": 0,
            "results": [],
        }
        fake_queue = MagicMock()

        with (
            patch("app.queue.is_memory_async_mode", return_value=True),
            patch("app.queue.get_memory_queue", return_value=fake_queue),
            patch(
                "app.routers.sdk.memory_extract_endpoint",
                new=AsyncMock(return_value=sync_result),
            ),
        ):
            res = app_client.post(
                "/sdk/v1/memory/extract?wait=true",
                json={
                    "response_text": "x" * 300,
                    "conversation_id": "wait-test",
                },
            )
        assert res.status_code == 200
        body = res.json()
        assert body == sync_result
        # Queue must NOT have been touched on the wait=true path
        fake_queue.enqueue.assert_not_called()

    def test_sync_default_when_queue_disabled(self, app_client):
        """When ``MEMORY_QUEUE_MODE=sync`` (the default), the endpoint
        runs sync regardless of the ``wait`` parameter — preserves the
        existing response envelope for every operator that hasn't opted
        into async yet."""
        sync_result = {
            "conversation_id": "sync-default",
            "timestamp": "2026-05-08T00:00:00Z",
            "memories_extracted": 0,
            "memories_stored": 0,
            "skipped_duplicates": 0,
            "results": [],
        }
        fake_queue = MagicMock()

        with (
            patch("app.queue.is_memory_async_mode", return_value=False),
            patch("app.queue.get_memory_queue", return_value=fake_queue),
            patch(
                "app.routers.sdk.memory_extract_endpoint",
                new=AsyncMock(return_value=sync_result),
            ),
        ):
            res = app_client.post(
                "/sdk/v1/memory/extract",
                json={
                    "response_text": "x" * 300,
                    "conversation_id": "sync-default",
                },
            )
        assert res.status_code == 200
        fake_queue.enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# Status endpoint — GET /sdk/v1/memory/extract/jobs/{job_id}
# ---------------------------------------------------------------------------


class TestMemoryExtractJobStatus:
    def _fake_job(self, *, status: str, result=None, error: str | None = None):
        job = MagicMock()
        job.id = "job-xyz"
        job.get_status.return_value = status
        job.enqueued_at = None
        job.started_at = None
        job.ended_at = None
        job.result = result
        job.exc_info = (error or "") if status == "failed" else None
        return job

    def test_queued_returns_status_only(self, app_client):
        fake_job = self._fake_job(status="queued")
        fake_queue = MagicMock()
        with (
            patch("app.queue.get_memory_queue", return_value=fake_queue),
            patch("rq.job.Job.fetch", return_value=fake_job),
        ):
            res = app_client.get("/sdk/v1/memory/extract/jobs/job-xyz")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "queued"
        assert body["result"] is None
        assert body["error"] is None

    def test_finished_returns_validated_result(self, app_client):
        worker_output = {
            "conversation_id": "done",
            "timestamp": "2026-05-08T00:00:01Z",
            "memories_extracted": 2,
            "memories_stored": 2,
            "skipped_duplicates": 0,
            "results": [],
        }
        fake_job = self._fake_job(status="finished", result=worker_output)
        fake_queue = MagicMock()
        with (
            patch("app.queue.get_memory_queue", return_value=fake_queue),
            patch("rq.job.Job.fetch", return_value=fake_job),
        ):
            res = app_client.get("/sdk/v1/memory/extract/jobs/job-xyz")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "finished"
        assert body["result"]["memories_extracted"] == 2
        assert body["result"]["memories_stored"] == 2
        assert body["error"] is None

    def test_failed_returns_error_summary(self, app_client):
        fake_job = self._fake_job(
            status="failed",
            error="Traceback (most recent call last):\nRuntimeError: oh no",
        )
        fake_queue = MagicMock()
        with (
            patch("app.queue.get_memory_queue", return_value=fake_queue),
            patch("rq.job.Job.fetch", return_value=fake_job),
        ):
            res = app_client.get("/sdk/v1/memory/extract/jobs/job-xyz")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "failed"
        assert body["result"] is None
        assert "RuntimeError: oh no" in (body["error"] or "")

    def test_unknown_job_id_returns_404(self, app_client):
        from rq.exceptions import NoSuchJobError

        fake_queue = MagicMock()
        with (
            patch("app.queue.get_memory_queue", return_value=fake_queue),
            patch("rq.job.Job.fetch", side_effect=NoSuchJobError("nope")),
        ):
            res = app_client.get("/sdk/v1/memory/extract/jobs/missing")
        assert res.status_code == 404
        assert "missing" in res.json()["detail"]


# ---------------------------------------------------------------------------
# Queue isolation — ingest and memory queues are independent
# ---------------------------------------------------------------------------


class TestQueueIsolation:
    """The systemic split: memory_extract jobs go on cerid-memory, ingest
    jobs go on cerid-ingest. A slow ingest can't stall a memory job
    waiting in the same queue."""

    def test_queue_names_are_distinct(self):
        from app.queue import INGEST_QUEUE_NAME, MEMORY_QUEUE_NAME
        assert INGEST_QUEUE_NAME != MEMORY_QUEUE_NAME

    def test_independent_async_mode_flags(self, monkeypatch):
        """Each queue has its own ``*_QUEUE_MODE`` env. Operators can opt
        into ingest async without enabling memory async, or vice versa."""
        # Force rq-available to isolate config-only behaviour
        import app.queue as q
        import config
        from app.queue import is_ingest_async_mode, is_memory_async_mode
        monkeypatch.setattr(q, "_rq_available", True)

        monkeypatch.setattr(config, "INGEST_QUEUE_MODE", "async")
        monkeypatch.setattr(config, "MEMORY_QUEUE_MODE", "sync")
        assert is_ingest_async_mode() is True
        assert is_memory_async_mode() is False

        monkeypatch.setattr(config, "INGEST_QUEUE_MODE", "sync")
        monkeypatch.setattr(config, "MEMORY_QUEUE_MODE", "async")
        assert is_ingest_async_mode() is False
        assert is_memory_async_mode() is True
