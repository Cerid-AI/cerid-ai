# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Private mode must not silently switch itself off when Redis hiccups.

``get_private_mode_level`` used to return 0 on any Redis exception, justified by
"the client applies its own skip logic independently of this server check". That
is true for the browser and false for everyone else: direct API, SDK and MCP
callers have no client-side skip, so a transient Redis error deactivated every
L1-L4 server-side guarantee for them — no durable-save suppression, no KB
isolation, no logging bypass — with nothing in the response indicating it.

The fix holds the last successfully-read level. That is sound because the level
lives in Redis: if Redis is unreachable, nobody can have changed it.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import app.services.private_mode as pm


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test starts from a cold process."""
    pm._last_known_level = 0
    yield
    pm._last_known_level = 0


def _redis_returning(value):
    fake = MagicMock()
    fake.get.return_value = value
    return fake


def test_reads_the_live_level_when_redis_is_healthy():
    with patch.object(pm, "get_redis", return_value=_redis_returning(b"3")):
        assert pm.get_private_mode_level() == 3


def test_redis_failure_holds_the_last_known_level():
    """The regression: a blip must not silently drop the user to level 0."""
    with patch.object(pm, "get_redis", return_value=_redis_returning(b"4")):
        assert pm.get_private_mode_level() == 4

    with patch.object(pm, "get_redis", side_effect=ConnectionError("redis down")):
        held = pm.get_private_mode_level()

    assert held == 4, (
        "private mode failed open to level 0 on a Redis error — every "
        "server-side privacy guarantee silently deactivated for API/SDK/MCP "
        "callers, who have no client-side fallback"
    )


def test_guarantees_stay_enforced_while_redis_is_down():
    """The level is the input to every gate — check one end-to-end."""
    with patch.object(pm, "get_redis", return_value=_redis_returning(b"2")):
        pm.get_private_mode_level()

    with patch.object(pm, "get_redis", side_effect=OSError("connection reset")):
        assert pm.private_blocks(1) is True
        assert pm.private_blocks(2) is True
        assert pm.private_blocks(3) is False  # honest about the actual level


def test_cold_start_with_redis_down_reports_zero():
    """No observed level yet — 0 is the only honest answer, and is logged."""
    with patch.object(pm, "get_redis", side_effect=ConnectionError("down")):
        assert pm.get_private_mode_level() == 0


def test_disabling_private_mode_is_not_overridden_by_the_cache():
    """A real 0 read must clear the cache, or the mode could never be turned off."""
    with patch.object(pm, "get_redis", return_value=_redis_returning(b"4")):
        assert pm.get_private_mode_level() == 4
    with patch.object(pm, "get_redis", return_value=_redis_returning(b"0")):
        assert pm.get_private_mode_level() == 0
    with patch.object(pm, "get_redis", side_effect=ConnectionError("down")):
        assert pm.get_private_mode_level() == 0, (
            "cache retained a stale non-zero level after the user disabled "
            "private mode — the mode would appear stuck on"
        )


def test_missing_key_is_treated_as_disabled_and_cached():
    with patch.object(pm, "get_redis", return_value=_redis_returning(None)):
        assert pm.get_private_mode_level() == 0
