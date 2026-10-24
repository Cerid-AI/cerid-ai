# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A source that answers nothing, call after call, is degraded — not healthy.

Round 5 item 5.9 / Studio audit W7: the registry keeps each source's recent
outcomes and reports ``degraded`` once the last ``DATA_SOURCE_DEAD_CALLS`` calls
inside ``DATA_SOURCE_DEAD_WINDOW_S`` all came back empty or failed. Since the
retrieval audit (F28/F29) a source raises instead of swallowing a failure into
``[]``, so a failure trips the ``datasource-*`` breaker and is counted apart
from a quiet call.
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
        "status": "unknown", "calls_in_window": 0, "zero_result_calls": 0, "failed_calls": 0,
        "last_error": None, "last_results_at": None,
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
        "status": "degraded", "calls_in_window": 3, "zero_result_calls": 3, "failed_calls": 0,
        "last_error": None, "last_results_at": None,
    }


class _Broken(DataSource):
    name = "broken"
    description = "fails every call"

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        raise ConnectionError("upstream refused")


@pytest.mark.asyncio
async def test_a_failing_source_is_reported_apart_from_a_quiet_one(clock, monkeypatch):
    """Sources raise on failure now (audit F28/F29), so the registry can tell
    a broken source from one with nothing to say."""
    from core.utils.circuit_breaker import get_breaker

    get_breaker("datasource-broken").reset()
    reg = DataSourceRegistry()
    reg.register(_Broken())
    reg.register(_Silent())

    assert await reg.query_all("q") == []

    broken, silent = reg.source_health("broken"), reg.source_health("silent")
    assert broken["failed_calls"] == 1 and broken["last_error"] == "ConnectionError"
    assert silent["failed_calls"] == 0 and silent["zero_result_calls"] == 1 and silent["last_error"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("module,cls,query", [
    ("duckduckgo", "DuckDuckGoSource", "caffeine"), ("openlibrary", "OpenLibrarySource", "caffeine"),
    ("pubchem", "PubChemSource", "caffeine"), ("finance", "ExchangeRatesSource", "USD to EUR exchange rate"),
])
async def test_a_public_source_raises_when_its_api_fails(monkeypatch, module, cls, query):
    import importlib

    import httpx

    async def refuse(self, request, *a, **k):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)
    source = getattr(importlib.import_module(f"app.data_sources.{module}"), cls)()

    with pytest.raises(httpx.HTTPError):
        await source.query(query)


def test_a_failure_label_never_carries_the_request_url():
    import httpx

    request = httpx.Request("GET", "https://api.wolframalpha.com/v1/result?appid=SECRET-KEY&i=my+private+question")
    exc = httpx.HTTPStatusError("boom", request=request, response=httpx.Response(403, request=request))

    label = base_module._failure_label(exc)

    assert label == "HTTPStatusError 403"
    assert "SECRET" not in label and "private" not in label
