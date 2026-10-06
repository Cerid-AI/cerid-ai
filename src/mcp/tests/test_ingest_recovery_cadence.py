# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The ingest_recovery cadence is a setting, defaulting to five minutes.

Round 5 item 2. The job ran every 60 s (256 runs in five hours on the live
stack with no ingest). Pending chunks are invisible to retrieval
(``PENDING_STATE_FILTER``), so a half-done ingest costs only the time until
its document becomes searchable; against that, the retry budget is two
attempts, so at 60 s a two-minute Neo4j outage dead-lettered every in-flight
ingest. Five minutes bounds the searchability delay and gives an outage ten
minutes before anything is purged.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("deps", MagicMock())

from app import scheduler as sched  # noqa: E402


def test_default_cadence_is_five_minutes():
    assert sched.config.INGEST_RECOVERY_INTERVAL_S == 300


@pytest.mark.asyncio
async def test_scheduler_registers_the_configured_cadence(monkeypatch):
    sched.stop_scheduler()
    monkeypatch.setattr(sched.config, "INGEST_RECOVERY_INTERVAL_S", 123)
    try:
        job = sched.start_scheduler().get_job("ingest_recovery")
        interval_s = job.trigger.interval.total_seconds()
    finally:
        sched.stop_scheduler()

    assert interval_s == 123
