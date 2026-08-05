# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for scheduled maintenance engine."""

import sys
from unittest.mock import MagicMock

import pytest

# Dependency stubs (chromadb, neo4j, redis, apscheduler, etc.) are handled
# by conftest.py pytest_configure(). We still need a deps stub since
# scheduler.py imports from deps at module level.
sys.modules.setdefault("deps", MagicMock())

from app.scheduler import get_job_status, start_scheduler, stop_scheduler  # noqa: E402


class TestSchedulerJobStatus:
    def test_not_running(self):
        """When scheduler isn't started, status should be not_running."""
        stop_scheduler()
        status = get_job_status()
        assert status["status"] == "not_running"
        assert status["jobs"] == []

    @pytest.mark.asyncio
    async def test_stop(self):
        """After stopping, scheduler should report not_running."""
        start_scheduler()
        stop_scheduler()
        status = get_job_status()
        assert status["status"] == "not_running"


class TestTriggerJob:
    """Contract for the manual job-trigger endpoint backing function.

    trigger_job resolves the callable from the live scheduler's own job
    record, so triggerability is defined by 'is it scheduled right now'.
    """

    def test_unknown_job_raises_keyerror(self, monkeypatch):
        """A job not live in the scheduler → KeyError (router maps to 404)."""
        from app import scheduler as sched

        fake_scheduler = MagicMock()
        fake_scheduler.get_job.return_value = None
        monkeypatch.setattr(sched, "_scheduler", fake_scheduler)
        monkeypatch.setattr(sched, "_manual_running", set())

        with pytest.raises(KeyError):
            sched.trigger_job("definitely_not_a_job")

    def test_scheduler_not_running_raises_valueerror(self, monkeypatch):
        """No live scheduler → ValueError (router maps to 409)."""
        from app import scheduler as sched

        monkeypatch.setattr(sched, "_scheduler", None)
        with pytest.raises(ValueError, match="not running"):
            sched.trigger_job("compute_umap_3d")

    def test_already_running_raises_valueerror(self, monkeypatch):
        """A second trigger while one is in flight → ValueError (coalesced)."""
        from app import scheduler as sched

        fake_scheduler = MagicMock()
        fake_scheduler.get_job.return_value = MagicMock(name="job")
        monkeypatch.setattr(sched, "_scheduler", fake_scheduler)
        monkeypatch.setattr(sched, "_manual_running", {"compute_umap_3d"})

        with pytest.raises(ValueError, match="already running"):
            sched.trigger_job("compute_umap_3d")

    def test_returns_started_payload_with_cache_patterns(self, monkeypatch):
        """A live job fires and reports the caches it will invalidate."""
        from app import scheduler as sched

        async def _fake_job():
            return None

        job = MagicMock()
        job.name = "Constellation 3D coordinate compute"
        job.func = _fake_job
        job.args = ()
        job.kwargs = {}
        fake_scheduler = MagicMock()
        fake_scheduler.get_job.return_value = job
        monkeypatch.setattr(sched, "_scheduler", fake_scheduler)
        monkeypatch.setattr(sched, "_manual_running", set())
        # Don't actually schedule the coroutine on a loop in this sync test.
        monkeypatch.setattr(sched.asyncio, "create_task", lambda coro: coro.close())

        result = sched.trigger_job("compute_umap_3d")
        assert result["status"] == "started"
        assert result["id"] == "compute_umap_3d"
        assert result["invalidates"] == ["cerid:graph:emb3d:*"]
