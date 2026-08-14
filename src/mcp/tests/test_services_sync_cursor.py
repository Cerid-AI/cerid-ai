# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Unit tests for app.services.sync_cursor — Redis-hot / Neo4j-durable cursor
store, including the AF-074 write-ordering reconciliation guarantee."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.services.sync_cursor import clear_cursor, get_cursor, set_cursor

_SOURCE_ID = "src-1"
_KEY = "source:cursor:src-1"


class TestSetCursor:
    def test_writes_neo4j_before_redis(self):
        """AF-074: Neo4j (durable) must be written before Redis (hot) so a
        crash between the two writes never leaves Redis ahead of Neo4j."""
        redis_client = MagicMock()
        driver = MagicMock()
        calls: list[str] = []
        redis_client.set.side_effect = lambda *a, **k: calls.append("redis")

        with patch(
            "app.services.sync_cursor.srcdb.update_source_cursor",
            side_effect=lambda *a, **k: calls.append("neo4j"),
        ) as mock_update:
            set_cursor(redis_client, driver, _SOURCE_ID, {"page": 2})

        assert calls == ["neo4j", "redis"]
        mock_update.assert_called_once_with(driver, _SOURCE_ID, {"page": 2})
        redis_client.set.assert_called_once_with(_KEY, json.dumps({"page": 2}))

    def test_redis_write_skipped_when_neo4j_write_fails(self):
        """A failed Neo4j write must NOT be followed by a Redis write — Redis
        must never hold a cursor value that was never durably committed."""
        redis_client = MagicMock()
        driver = MagicMock()

        with patch(
            "app.services.sync_cursor.srcdb.update_source_cursor",
            side_effect=RuntimeError("neo4j unavailable"),
        ):
            set_cursor(redis_client, driver, _SOURCE_ID, {"page": 3})

        redis_client.set.assert_not_called()

    def test_redis_failure_after_successful_neo4j_write_does_not_raise(self):
        redis_client = MagicMock()
        redis_client.set.side_effect = RuntimeError("redis unavailable")
        driver = MagicMock()

        with patch("app.services.sync_cursor.srcdb.update_source_cursor") as mock_update:
            set_cursor(redis_client, driver, _SOURCE_ID, {"page": 4})

        mock_update.assert_called_once()

    def test_none_redis_client_still_writes_neo4j(self):
        driver = MagicMock()

        with patch("app.services.sync_cursor.srcdb.update_source_cursor") as mock_update:
            set_cursor(None, driver, _SOURCE_ID, {"page": 5})

        mock_update.assert_called_once_with(driver, _SOURCE_ID, {"page": 5})


class TestGetCursor:
    def test_prefers_redis_on_hit(self):
        redis_client = MagicMock()
        redis_client.get.return_value = json.dumps({"page": 1})
        driver = MagicMock()

        with patch("app.services.sync_cursor.srcdb.get_source") as mock_get_source:
            result = get_cursor(redis_client, driver, _SOURCE_ID)

        assert result == {"page": 1}
        mock_get_source.assert_not_called()

    def test_falls_back_to_neo4j_and_warms_redis_on_miss(self):
        """AF-074: a missing/evicted Redis entry is repaired from Neo4j —
        the reconciliation path set_cursor's Neo4j-first ordering relies on."""
        redis_client = MagicMock()
        redis_client.get.return_value = None
        driver = MagicMock()

        with patch(
            "app.services.sync_cursor.srcdb.get_source",
            return_value={"sync_cursor": {"page": 7}},
        ):
            result = get_cursor(redis_client, driver, _SOURCE_ID)

        assert result == {"page": 7}
        redis_client.set.assert_called_once_with(_KEY, json.dumps({"page": 7}))

    def test_both_stores_empty_returns_empty_dict(self):
        redis_client = MagicMock()
        redis_client.get.return_value = None
        driver = MagicMock()

        with patch("app.services.sync_cursor.srcdb.get_source", return_value=None):
            result = get_cursor(redis_client, driver, _SOURCE_ID)

        assert result == {}


class TestClearCursor:
    def test_clears_both_stores(self):
        redis_client = MagicMock()
        driver = MagicMock()

        with patch("app.services.sync_cursor.srcdb.update_source_cursor") as mock_update:
            clear_cursor(redis_client, driver, _SOURCE_ID)

        redis_client.delete.assert_called_once_with(_KEY)
        mock_update.assert_called_once_with(driver, _SOURCE_ID, {})
