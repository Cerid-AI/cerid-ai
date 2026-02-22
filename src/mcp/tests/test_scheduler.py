"""Tests for scheduled maintenance engine (Phase 4C.1)."""

import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock heavy dependencies before importing scheduler
sys.modules.setdefault("chromadb", MagicMock())
sys.modules.setdefault("chromadb.config", MagicMock())
sys.modules.setdefault("redis", MagicMock())
sys.modules.setdefault("neo4j", MagicMock())
sys.modules.setdefault("deps", MagicMock())

from scheduler import get_job_status, start_scheduler, stop_scheduler


class TestSchedulerJobStatus:
    def test_not_running(self):
        """When scheduler isn't started, status should be not_running."""
        stop_scheduler()
        status = get_job_status()
        assert status["status"] == "not_running"
        assert status["jobs"] == []

    @pytest.mark.asyncio
    async def test_start_and_status(self):
        """Scheduler should report running with configured jobs."""
        sched = start_scheduler()
        try:
            status = get_job_status()
            assert status["status"] == "running"
            job_ids = {j["id"] for j in status["jobs"]}
            assert "rectify" in job_ids
            assert "health_check" in job_ids
            assert "stale_detection" in job_ids
        finally:
            stop_scheduler()

    @pytest.mark.asyncio
    async def test_stop(self):
        """After stopping, scheduler should report not_running."""
        start_scheduler()
        stop_scheduler()
        status = get_job_status()
        assert status["status"] == "not_running"
