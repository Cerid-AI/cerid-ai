# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A source that answers nothing, call after call, is degraded — not healthy.

Round 5 item 5.9 / Studio audit W7: ``DuckDuckGoSource.query`` swallows every
failure and returns ``[]``, so the ``datasource-*`` breaker never trips and the
source lists as healthy while contributing nothing. The registry now keeps
each source's recent result counts and reports ``degraded`` once the last
``DATA_SOURCE_DEAD_CALLS`` calls inside ``DATA_SOURCE_DEAD_WINDOW_S`` all came
back empty.
"""
from __future__ import annotations

import pytest

from app.data_sources import base as base_module
from app.data_sources.base import DataSource, DataSourceRegistry, DataSourceResult


class _Silent(DataSource):
    name = "silent"
    description = "answers nothing"

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        return []


class _Talkative(DataSource):
    name = "talkative"
    description = "answers once"

    def __init__(self) -> None:
        self.answers = 1

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        if self.answers:
            self.answers -= 1
            return [DataSourceResult("t", "c", source_name="talkative")]
        return []


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1_000_000.0}
    monkeypatch.setattr(base_module.time, "time", lambda: now["t"])
    monkeypatch.setattr(base_module.config, "DATA_SOURCE_DEAD_CALLS", 3)
    monkeypatch.setattr(base_module.config, "DATA_SOURCE_DEAD_WINDOW_S", 600)
    return now


@pytest.fixture
def registry(clock):
    reg = DataSourceRegistry()
    reg.register(_Silent())
    reg.register(_Talkative())
    return reg


def test_never_queried_source_is_unknown(registry):
    assert registry.source_health("silent") == {
        "status": "unknown", "calls_in_window": 0, "zero_result_calls": 0, "last_results_at": None,
    }


@pytest.mark.asyncio
async def test_source_turns_degraded_after_n_empty_calls(registry):
    for _ in range(2):
        await registry.query_all("anything")
    assert registry.source_health("silent")["status"] == "healthy"

    await registry.query_all("anything")

    health = registry.source_health("silent")
    assert health["status"] == "degraded"
    assert health["zero_result_calls"] == 3
    assert health["calls_in_window"] == 3


@pytest.mark.asyncio
async def test_one_answer_inside_the_window_keeps_the_source_healthy(registry, clock):
    for _ in range(3):
        await registry.query_all("anything")

    health = registry.source_health("talkative")
    assert health["status"] == "healthy"
    assert health["zero_result_calls"] == 2
    assert health["last_results_at"] is not None


@pytest.mark.asyncio
async def test_empty_calls_outside_the_window_are_forgotten(registry, clock):
    for _ in range(3):
        await registry.query_all("anything")
    assert registry.source_health("silent")["status"] == "degraded"

    clock["t"] += 601
    await registry.query_all("anything")

    health = registry.source_health("silent")
    assert health["status"] == "healthy"
    assert health["calls_in_window"] == 1


@pytest.mark.asyncio
async def test_list_sources_carries_the_health(registry):
    for _ in range(3):
        await registry.query_all("anything")

    by_name = {s["name"]: s for s in registry.list_sources()}
    assert by_name["silent"]["health"]["status"] == "degraded"
    assert by_name["silent"]["health"]["zero_result_calls"] == 3
    assert by_name["talkative"]["health"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_data_sources_endpoint_shows_the_degraded_source(registry, monkeypatch):
    import app.data_sources as pkg
    from app.routers.data_sources import list_data_sources

    monkeypatch.setattr(pkg, "registry", registry)
    monkeypatch.setattr(pkg, "_get_redis_client", lambda: None)
    for _ in range(3):
        await registry.query_all("anything")

    payload = await list_data_sources()

    silent = next(s for s in payload["sources"] if s["name"] == "silent")
    assert silent["health"] == {
        "status": "degraded", "calls_in_window": 3, "zero_result_calls": 3, "last_results_at": None,
    }
