# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the RQ ingestion queue (Workstream E Phase 5a).

The queue is opt-in (INGEST_QUEUE_MODE=async) and decoupled from the
live rq-worker / Redis stack — these tests cover the pure surface
(mode-detection logic, task body's coroutine bridge) without
requiring an actual Redis connection or running worker.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import queue as queue_pkg
from app.queue import tasks as queue_tasks


def test_is_async_mode_default_is_false(monkeypatch):
    """Without INGEST_QUEUE_MODE=async, the queue path is dormant."""
    import config

    monkeypatch.setattr(config, "INGEST_QUEUE_MODE", "sync")
    assert queue_pkg.is_async_mode() is False


def test_is_async_mode_falsy_when_rq_missing(monkeypatch):
    """Even with INGEST_QUEUE_MODE=async, missing rq returns False."""
    import config

    monkeypatch.setattr(config, "INGEST_QUEUE_MODE", "async")
    monkeypatch.setattr(queue_pkg, "_rq_available", False)
    assert queue_pkg.is_async_mode() is False


def test_is_async_mode_true_when_both_set(monkeypatch):
    """async + rq available → True."""
    import config

    monkeypatch.setattr(config, "INGEST_QUEUE_MODE", "async")
    monkeypatch.setattr(queue_pkg, "_rq_available", True)
    assert queue_pkg.is_async_mode() is True


def test_get_ingest_queue_raises_when_rq_missing(monkeypatch):
    """get_ingest_queue is honest about the missing dep."""
    monkeypatch.setattr(queue_pkg, "_rq_available", False)
    monkeypatch.setattr(queue_pkg, "_queue_singleton", None)
    with pytest.raises(RuntimeError, match="rq not installed"):
        queue_pkg.get_ingest_queue()


def test_ingest_file_task_bridges_async_to_sync():
    """The task body runs the async ingest_file via a fresh event loop."""
    fake_result = {"status": "success", "artifact_id": "art-123", "chunks": 4}
    fake_ingest = AsyncMock(return_value=fake_result)

    with patch("app.services.ingestion.ingest_file", fake_ingest):
        result = queue_tasks.ingest_file_task(
            file_path="/path/to/file.txt",
            domain="general",
        )

    assert result == fake_result
    fake_ingest.assert_awaited_once()
    _, kwargs = fake_ingest.call_args
    assert kwargs["file_path"] == "/path/to/file.txt"
    assert kwargs["domain"] == "general"


def test_ingest_content_task_calls_sync_service():
    """ingest_content is sync; the task forwards directly."""
    fake_result = {"status": "success", "artifact_id": "art-456"}
    fake_ingest = MagicMock(return_value=fake_result)

    with patch("app.services.ingestion.ingest_content", fake_ingest):
        result = queue_tasks.ingest_content_task(
            content="hello world",
            domain="general",
            metadata={"client_source": "queue-smoke"},
        )

    assert result == fake_result
    fake_ingest.assert_called_once_with(
        "hello world", "general", {"client_source": "queue-smoke"},
    )
