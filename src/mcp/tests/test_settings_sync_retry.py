# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Task 25 / audit P1-11: settings.json writes must retry on macOS
Dropbox advisory-lock collisions (OSError EDEADLK / errno 35).

Previously the router caught the exception, logged a warning, and dropped
the user's write. After this patch, ``write_settings_with_retry`` retries
up to 3 times with exponential backoff and only surfaces a final-failure
log (which the GUI can render) on exhaustion.
"""
from __future__ import annotations

import errno
from unittest.mock import patch

import pytest

from app.sync.user_state import write_settings_with_retry


@pytest.mark.asyncio
async def test_retries_edeadlk_then_succeeds(tmp_path):
    """Mock write_settings to raise EDEADLK twice then succeed.

    The retry wrapper must absorb the first two failures and land the
    third attempt, returning True.
    """
    call_count = {"n": 0}

    def fake_write_settings(sync_dir: str, settings: dict) -> None:
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise OSError(errno.EDEADLK, "Resource deadlock avoided")
        # Third call succeeds — no-op.

    # Patch the *module-level* symbol so asyncio.to_thread(write_settings, ...)
    # resolves to our fake via the retry wrapper's imported name.
    with (
        patch("app.sync.user_state.write_settings", side_effect=fake_write_settings),
        patch(
            "app.sync.user_state.asyncio.sleep",
            # Skip real sleeps so the test runs instantly while still
            # exercising the backoff path.
            new=_noop_sleep,
        ),
    ):
        result = await write_settings_with_retry(
            str(tmp_path), {"rag_mode": "smart"}
        )

    assert result is True, "retry wrapper did not recover after two EDEADLK failures"
    assert call_count["n"] == 3, (
        f"expected exactly 3 write attempts, got {call_count['n']}"
    )


@pytest.mark.asyncio
async def test_retries_exhaust_and_log_warning(tmp_path, caplog):
    """After _EDEADLK_RETRY_ATTEMPTS consecutive EDEADLKs the wrapper
    must return False and emit a structured warning the GUI can surface.
    """
    def always_deadlocked(sync_dir: str, settings: dict) -> None:
        raise OSError(errno.EDEADLK, "Resource deadlock avoided")

    with (
        patch("app.sync.user_state.write_settings", side_effect=always_deadlocked),
        patch("app.sync.user_state.asyncio.sleep", new=_noop_sleep),
        caplog.at_level("WARNING", logger="ai-companion.sync.user_state"),
    ):
        result = await write_settings_with_retry(
            str(tmp_path), {"rag_mode": "smart"}
        )

    assert result is False
    # The final-failure log carries the structured event name the GUI
    # can filter on.
    assert any(
        "settings.sync_write_failed_edeadlk" in rec.getMessage()
        for rec in caplog.records
    ), (
        "expected a 'settings.sync_write_failed_edeadlk' warning on "
        f"exhaustion; saw: {[r.getMessage() for r in caplog.records]}"
    )


@pytest.mark.asyncio
async def test_non_edeadlk_oserror_is_not_retried(tmp_path):
    """A non-EDEADLK OSError (e.g. EACCES) must propagate on the first
    attempt — we don't want to mask real permission / disk errors behind
    a retry loop.
    """
    call_count = {"n": 0}

    def permission_denied(sync_dir: str, settings: dict) -> None:
        call_count["n"] += 1
        raise OSError(errno.EACCES, "Permission denied")

    with patch(
        "app.sync.user_state.write_settings", side_effect=permission_denied
    ):
        with pytest.raises(OSError) as excinfo:
            await write_settings_with_retry(
                str(tmp_path), {"rag_mode": "smart"}
            )

    assert excinfo.value.errno == errno.EACCES
    assert call_count["n"] == 1, (
        "non-EDEADLK OSError was retried — this would mask genuine errors"
    )


async def _noop_sleep(*_a, **_k) -> None:
    """Awaitable no-op used as an asyncio.sleep replacement in tests."""
    return None
