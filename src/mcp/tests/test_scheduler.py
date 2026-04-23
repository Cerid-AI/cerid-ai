# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

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
