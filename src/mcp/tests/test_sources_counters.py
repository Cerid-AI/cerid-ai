# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""AF-023 — the :Source counters.

``total_artifacts_24h`` used to be a write-time accumulator nothing ever
decremented (a lifetime total mislabeled as a 24h window). It's now computed
at read time by ``count_artifacts_last_24h``, and ``increment_source_counters``
no longer touches it.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from app.db.neo4j.sources import count_artifacts_last_24h, increment_source_counters
from app.routers.sources import _to_record


def _node(**over: object) -> dict:
    base = {
        "id": "src-1",
        "kind": "rss_feed",
        "display_name": "Example feed",
    }
    base.update(over)
    return base


class TestToRecordLive24hOverride:
    def test_override_wins_over_stale_stored_value(self) -> None:
        rec = _to_record(_node(total_artifacts_24h=999), total_artifacts_24h=2)
        assert rec.total_artifacts_24h == 2

    def test_omitted_override_falls_back_to_dict(self) -> None:
        rec = _to_record(_node(total_artifacts_24h=5))
        assert rec.total_artifacts_24h == 5

    def test_omitted_override_defaults_to_zero_when_absent(self) -> None:
        rec = _to_record(_node())
        assert rec.total_artifacts_24h == 0


class TestCountArtifactsLast24h:
    def test_queries_from_source_edges_and_coerces_to_int(self) -> None:
        driver = MagicMock()
        session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = {"c": 7}

        result = count_artifacts_last_24h(driver, "src-1")

        assert result == 7
        cypher = session.run.call_args.args[0]
        assert "FROM_SOURCE" in cypher
        assert "a.ingested_at >= $since" in cypher
        assert session.run.call_args.kwargs.get("id") == "src-1"

    def test_no_record_returns_zero(self) -> None:
        driver = MagicMock()
        session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        session.run.return_value.single.return_value = None

        assert count_artifacts_last_24h(driver, "src-1") == 0


class TestIncrementSourceCountersNoLongerTouches24h:
    def test_query_does_not_reference_total_artifacts_24h(self) -> None:
        driver = MagicMock()
        session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)

        increment_source_counters(driver, "src-1", artifacts=1, chunks=2, edges=3)

        cypher = session.run.call_args.args[0]
        assert "total_artifacts_24h" not in cypher
        assert "total_edges" in cypher
        assert session.run.call_args.kwargs.get("edges") == 3
