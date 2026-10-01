# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A data source the operator disabled stays disabled after a restart.

The enabled flags live in Redis. A restarted process starts from the code
default (every source enabled), so startup has to load the persisted flags
before anything queries the registry.
"""
from __future__ import annotations

import fakeredis
import pytest

import app.data_sources as ds
from app.data_sources import persist_enabled_state, registry
from app.data_sources.base import DataSource, DataSourceResult
from app.main import _prewarm_external_sources


class _RecordingSource(DataSource):
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"Recording stub {name}"
        self.enabled = True
        self.queries: list[str] = []

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        self.queries.append(query)
        return []


@pytest.fixture
def restarted_process(monkeypatch: pytest.MonkeyPatch):
    """Redis remembers 'startup_off' as disabled; the registry is fresh from import."""
    redis = fakeredis.FakeRedis(decode_responses=True)
    assert persist_enabled_state("startup_off", False, redis_client=redis)

    off = _RecordingSource("startup_off")
    on = _RecordingSource("startup_on")
    monkeypatch.setattr(registry, "_sources", {off.name: off, on.name: on})
    monkeypatch.setattr(ds, "_hydrated", False)
    monkeypatch.setattr(ds, "_get_redis_client", lambda: redis)
    return off, on


async def test_startup_does_not_query_a_source_the_operator_disabled(restarted_process) -> None:
    off, on = restarted_process

    await _prewarm_external_sources()

    assert on.queries, "the enabled source should have been probed"
    assert off.queries == []


async def test_disabled_source_stays_out_of_later_queries(restarted_process) -> None:
    off, on = restarted_process

    await _prewarm_external_sources()

    assert [s.name for s in registry.get_enabled_sources()] == ["startup_on"]
    await registry.query_all("anything")
    assert off.queries == []
