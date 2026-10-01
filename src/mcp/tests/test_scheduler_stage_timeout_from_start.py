# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""F187: a graph-pipeline stage's completion budget runs from when the job
starts, not from when it was enqueued.

Every nightly stage was logged as 'timeout' because the jobs sat in the
processor queue for longer than the budget before a worker picked them up,
then completed in seconds.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("deps", MagicMock())

from app import scheduler as sched  # noqa: E402
from core.processor.job import JobState  # noqa: E402

_T0 = 1_800_000_000.0
_POLL_S = 5.0
_BUDGET_S = 600.0


class _Clock:
    """Wall clock that only moves when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = _T0

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


class _Queue:
    """Processor queue whose job starts and finishes at set clock offsets."""

    def __init__(self, clock: _Clock, *, starts_after: float | None, runs_for: float | None) -> None:
        self._clock = clock
        self._starts_at = None if starts_after is None else _T0 + starts_after
        self._ends_at = (
            None if self._starts_at is None or runs_for is None else self._starts_at + runs_for
        )

    async def enqueue_if_absent(self, record) -> str:
        return "job-1"

    def _record(self) -> SimpleNamespace:
        now = self._clock.now
        started = self._starts_at is not None and now >= self._starts_at
        ended = self._ends_at is not None and now >= self._ends_at
        state = JobState.COMPLETED if ended else JobState.RUNNING if started else JobState.PENDING
        return SimpleNamespace(
            id="job-1",
            state=state,
            error_message=None,
            started_at=datetime.fromtimestamp(self._starts_at, tz=timezone.utc) if started else None,
        )

    async def get(self, job_id: str) -> SimpleNamespace:
        return self._record()

    async def list_recent(self, limit: int) -> list[SimpleNamespace]:
        record = self._record()
        return [record] if record.state == JobState.COMPLETED else []


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(sched, "time", SimpleNamespace(time=clock.time))
    monkeypatch.setattr(sched, "asyncio", SimpleNamespace(sleep=clock.sleep))
    monkeypatch.setattr(sched, "_STAGE_COMPLETION_POLL_S", _POLL_S)
    monkeypatch.setattr(sched, "_STAGE_COMPLETION_TIMEOUT_S", _BUDGET_S)
    monkeypatch.setattr(sched.config, "PROCESSOR_PENDING_STALE_TTL_S", 21600)
    return clock


@pytest.fixture
def log(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []
    monkeypatch.setattr(sched, "_log_execution", lambda *a, **k: calls.append(a))
    return calls


async def _run(queue: _Queue) -> None:
    job = MagicMock()
    await sched._enqueue_and_await_completion(queue, job, "compute_entity_embeddings", _T0)


def _statuses(log: list[tuple]) -> list[str]:
    return [call[1] for call in log]


@pytest.mark.asyncio
async def test_job_that_waits_in_the_queue_then_runs_quickly_is_a_success(clock, log):
    """The live case: 27 minutes queued, 18 seconds running."""
    await _run(_Queue(clock, starts_after=27 * 60, runs_for=18))

    assert "timeout" not in _statuses(log)
    assert _statuses(log)[-1] == "success"


@pytest.mark.asyncio
async def test_job_that_runs_past_the_budget_is_a_timeout(clock, log):
    await _run(_Queue(clock, starts_after=27 * 60, runs_for=None))

    assert _statuses(log)[-1] == "timeout"
    assert "of starting" in log[-1][3]
    # Gave up one budget after the job started, not one budget after enqueue.
    assert clock.now - _T0 == pytest.approx(27 * 60 + _BUDGET_S, abs=2 * _POLL_S)


@pytest.mark.asyncio
async def test_job_that_never_starts_is_a_timeout_at_the_queue_stale_age(clock, log):
    await _run(_Queue(clock, starts_after=None, runs_for=None))

    assert _statuses(log)[-1] == "timeout"
    assert "not observed started" in log[-1][3]
    assert clock.now - _T0 == pytest.approx(21600, abs=2 * _POLL_S)
