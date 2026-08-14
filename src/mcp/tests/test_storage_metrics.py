# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for app/services/storage_metrics.py (AF-042).

Covers the shared usage-pct/threshold classification (the piece both
GET /system/storage and the ingest backpressure check depend on) and the
report's cache-hit / cache-miss / injected-getter behavior.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.services.storage_metrics import classify_storage_status, get_storage_report

# ---------------------------------------------------------------------------
# Tests: classify_storage_status — boundary behavior
# ---------------------------------------------------------------------------

class TestClassifyStorageStatus:
    def test_below_warn_is_healthy(self):
        assert classify_storage_status(59.9, warn_pct=60, critical_pct=80) == "healthy"

    def test_zero_is_healthy(self):
        assert classify_storage_status(0, warn_pct=60, critical_pct=80) == "healthy"

    def test_at_warn_boundary_is_warning(self):
        assert classify_storage_status(60, warn_pct=60, critical_pct=80) == "warning"

    def test_between_warn_and_critical_is_warning(self):
        assert classify_storage_status(79.9, warn_pct=60, critical_pct=80) == "warning"

    def test_at_critical_boundary_is_critical(self):
        assert classify_storage_status(80, warn_pct=60, critical_pct=80) == "critical"

    def test_above_critical_is_critical(self):
        assert classify_storage_status(150, warn_pct=60, critical_pct=80) == "critical"


# ---------------------------------------------------------------------------
# Tests: get_storage_report — cache + injected getters
# ---------------------------------------------------------------------------

class TestGetStorageReport:
    def test_cache_hit_skips_store_getters(self):
        """A warm Redis cache short-circuits before any store getter runs."""
        cached_payload = {"status": "healthy", "usage_pct": 12.3, "total_mb": 10}
        redis_mock = MagicMock()
        redis_mock.get.return_value = json.dumps(cached_payload)

        chroma_fn = MagicMock(side_effect=AssertionError("should not be called on cache hit"))
        neo4j_fn = MagicMock(side_effect=AssertionError("should not be called on cache hit"))

        result = get_storage_report(
            get_redis_fn=lambda: redis_mock,
            get_chroma_fn=chroma_fn,
            get_neo4j_fn=neo4j_fn,
        )

        assert result == cached_payload
        chroma_fn.assert_not_called()
        neo4j_fn.assert_not_called()

    @patch("app.services.storage_metrics._dir_size_mb", return_value=0.0)
    def test_cache_miss_computes_fresh_and_writes_cache(self, _mock_dir_size):
        redis_mock = MagicMock()
        redis_mock.get.return_value = None
        redis_mock.info.return_value = {"used_memory": 0, "used_memory_peak": 0}
        redis_mock.dbsize.return_value = 0

        chroma_client = MagicMock()
        chroma_client.list_collections.return_value = []

        neo4j_driver = MagicMock()
        session = MagicMock()
        neo4j_driver.session.return_value.__enter__ = MagicMock(return_value=session)
        neo4j_driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = {"c": 0}

        result = get_storage_report(
            get_redis_fn=lambda: redis_mock,
            get_chroma_fn=lambda: chroma_client,
            get_neo4j_fn=lambda: neo4j_driver,
        )

        assert result["status"] == "healthy"
        assert result["total_mb"] == 0
        redis_mock.setex.assert_called_once()

    def test_injected_getters_used_over_defaults(self):
        """The passed-through getters win — no fallback to app.deps singletons."""
        redis_mock = MagicMock()
        redis_mock.get.return_value = None
        redis_mock.info.return_value = {"used_memory": 0, "used_memory_peak": 0}
        redis_mock.dbsize.return_value = 0

        chroma_client = MagicMock()
        chroma_client.list_collections.return_value = []
        chroma_fn = MagicMock(return_value=chroma_client)

        neo4j_fn = MagicMock(return_value=None)  # disabled Neo4j

        result = get_storage_report(
            get_redis_fn=lambda: redis_mock,
            get_chroma_fn=chroma_fn,
            get_neo4j_fn=neo4j_fn,
        )

        chroma_fn.assert_called_once()
        neo4j_fn.assert_called_once()
        assert result["neo4j"]["status"] == "disabled"
