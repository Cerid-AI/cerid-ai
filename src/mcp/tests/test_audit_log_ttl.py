# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for Redis audit log TTL."""
from __future__ import annotations

from unittest.mock import MagicMock


def test_log_event_sets_ttl():
    """log_event should set a TTL on the ingest log key via pipeline."""
    from utils.cache import log_event

    mock_pipe = MagicMock()
    mock_redis = MagicMock()
    mock_redis.pipeline.return_value = mock_pipe
    log_event(mock_redis, "ingest", artifact_id="a1", domain="code", filename="test.py")
    # Verify pipeline was used
    mock_redis.pipeline.assert_called_once()
    mock_pipe.lpush.assert_called_once()
    mock_pipe.ltrim.assert_called_once()
    # Verify expire was called with 30-day TTL
    mock_pipe.expire.assert_called_once()
    args = mock_pipe.expire.call_args
    assert args[0][1] == 86400 * 30
    mock_pipe.execute.assert_called_once()
