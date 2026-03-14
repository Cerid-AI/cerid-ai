# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for Redis audit log TTL."""
from __future__ import annotations

from unittest.mock import MagicMock


def test_log_event_sets_ttl():
    """log_event should set a TTL on the ingest log key."""
    from utils.cache import log_event

    mock_redis = MagicMock()
    log_event(mock_redis, "ingest", artifact_id="a1", domain="code", filename="test.py")
    # Verify expire was called on the log key
    mock_redis.expire.assert_called_once()
    # TTL should be 30 days (2592000 seconds)
    args = mock_redis.expire.call_args
    assert args[0][1] == 86400 * 30
