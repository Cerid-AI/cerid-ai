# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The scheduled IMAP poll job (`_run_email_poll`) drives the existing
`poll_email()` on a cadence and maps its result status to a job-execution log
line. It must self-skip cleanly when no mailbox is configured so the job is
safe to register unconditionally."""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# scheduler.py imports from `deps` at module level (see test_scheduler.py).
sys.modules.setdefault("deps", MagicMock())

from app import scheduler as sched  # noqa: E402


def _capture_log(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []
    monkeypatch.setattr(
        sched, "_log_execution", lambda *a, **k: calls.append(a)
    )
    return calls


def _patch_poll(monkeypatch, result: dict) -> None:
    monkeypatch.setattr(
        "app.data_sources.email_imap.poll_email",
        AsyncMock(return_value=result),
    )


@pytest.mark.asyncio
async def test_skips_when_not_configured(monkeypatch) -> None:
    calls = _capture_log(monkeypatch)
    _patch_poll(monkeypatch, {"status": "not_configured", "messages": 0})
    await sched._run_email_poll()
    assert calls and calls[0][0] == "email_poll" and calls[0][1] == "skipped"


@pytest.mark.asyncio
async def test_skips_when_circuit_open(monkeypatch) -> None:
    calls = _capture_log(monkeypatch)
    _patch_poll(monkeypatch, {"status": "circuit_open", "messages": 0})
    await sched._run_email_poll()
    assert calls[0][1] == "skipped"


@pytest.mark.asyncio
async def test_success_logs_ingested_count(monkeypatch) -> None:
    calls = _capture_log(monkeypatch)
    _patch_poll(monkeypatch, {"status": "ok", "messages": 3})
    await sched._run_email_poll()
    assert calls[0][1] == "success"
    assert "ingested=3" in calls[0][3]


@pytest.mark.asyncio
async def test_poll_error_status_logged_as_error(monkeypatch) -> None:
    calls = _capture_log(monkeypatch)
    _patch_poll(monkeypatch, {"status": "error", "error": "imap down", "messages": 0})
    await sched._run_email_poll()
    assert calls[0][1] == "error"


@pytest.mark.asyncio
async def test_exception_is_caught_and_logged(monkeypatch) -> None:
    calls = _capture_log(monkeypatch)
    monkeypatch.setattr(
        "app.data_sources.email_imap.poll_email",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    await sched._run_email_poll()  # must not raise
    assert calls[0][1] == "error"


@pytest.mark.asyncio
async def test_exception_reaches_swallowed_error_counter(monkeypatch) -> None:
    """AF-039: the outer catch must also call log_swallowed_error, not just
    _log_execution — otherwise the failure is invisible to
    /health.swallowed_errors_last_hour."""
    swallowed_calls: list[tuple] = []
    monkeypatch.setattr(
        sched, "log_swallowed_error", lambda *a, **k: swallowed_calls.append(a)
    )
    monkeypatch.setattr(
        "app.data_sources.email_imap.poll_email",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    await sched._run_email_poll()
    assert swallowed_calls and swallowed_calls[0][0] == "app.scheduler"
