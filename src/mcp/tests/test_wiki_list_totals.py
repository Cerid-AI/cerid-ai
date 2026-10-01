# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""``total`` on the wiki list endpoints counts what matches, not what fit.

/wiki/index, /wiki/log and /wiki/contradictions each returned
``total=len(page)``, so ``total`` could never exceed the number of rows
returned and a client could not tell a complete list from a truncated one.

Only the Neo4j driver is faked here. The fake holds more rows than the
requested limit and answers the two kinds of query the code sends: a
``count(...)`` query and a row query that honours its limit parameter.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _CountResult:
    def __init__(self, total: int) -> None:
        self._total = total

    def single(self) -> dict[str, int]:
        return {"total": self._total}


class _FakeSession:
    def __init__(self, rows: list[dict[str, Any]], calls: list[tuple[str, dict[str, Any]]]) -> None:
        self._rows = rows
        self._calls = calls

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def run(self, cypher: str, **params: Any) -> Any:
        self._calls.append((cypher, params))
        if "count(" in cypher.lower() and "limit" not in cypher.lower():
            return _CountResult(len(self._rows))
        limit = params.get("limit", params.get("lim"))
        return iter(self._rows[:limit])


class _FakeDriver:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def session(self) -> _FakeSession:
        return _FakeSession(self.rows, self.calls)

    def count_calls(self) -> list[tuple[str, dict[str, Any]]]:
        return [c for c in self.calls if "limit" not in c[0].lower()]


def _client(monkeypatch: pytest.MonkeyPatch, driver: _FakeDriver, router: Any) -> TestClient:
    import app.deps as deps

    monkeypatch.setattr(deps, "get_neo4j", lambda: driver)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_wiki_index_total_counts_every_matching_entity(monkeypatch):
    from app.routers.wiki import router

    driver = _FakeDriver([
        {
            "canonical_id": f"other:e{i}",
            "name": f"Entity {i}",
            "entity_type": "OTHER",
            "mention_count": 1,
            "recent_activity_score": 0,
        }
        for i in range(7)
    ])
    res = _client(monkeypatch, driver, router).get("/wiki/index?limit=3&q=Entity")

    assert res.status_code == 200
    body = res.json()
    assert len(body["entries"]) == 3
    assert body["total"] == 7

    # The count must be taken over the same filters as the page, or
    # "3 of 7" would compare a filtered page with an unfiltered corpus.
    (_, count_params), = driver.count_calls()
    assert count_params["search"] == "entity"
    assert "hidden" in count_params


def test_wiki_index_total_includes_hidden_domains_when_asked(monkeypatch):
    from app.routers.wiki import router

    driver = _FakeDriver([])
    res = _client(monkeypatch, driver, router).get("/wiki/index?include_internal=true")

    assert res.status_code == 200
    (count_cypher, count_params), = driver.count_calls()
    assert "hidden" not in count_params
    assert "$hidden" not in count_cypher


def test_wiki_log_total_counts_every_matching_entry(monkeypatch):
    from app.routers.wiki import router

    driver = _FakeDriver([
        {
            "log_id": f"log{i}",
            "ts": f"2026-09-0{i + 1}T00:00:00+00:00",
            "action": "refresh",
            "entity_slug": "other:e1",
            "summary": "",
            "source_artifact_id": "",
        }
        for i in range(5)
    ])
    res = _client(monkeypatch, driver, router).get(
        "/wiki/log?limit=2&entity_slug=other:e1&since=2026-09-01",
    )

    assert res.status_code == 200
    body = res.json()
    assert len(body["entries"]) == 2
    assert body["total"] == 5

    (_, count_params), = driver.count_calls()
    assert count_params["slug"] == "other:e1"
    assert count_params["since"] == "2026-09-01"


def test_wiki_contradictions_total_counts_every_matching_finding(monkeypatch):
    from app.routers.contradictions import router

    driver = _FakeDriver([
        {"f": {"finding_id": f"f{i}", "severity": "medium", "detected_at": "2026-09-01"}}
        for i in range(6)
    ])
    res = _client(monkeypatch, driver, router).get(
        "/wiki/contradictions?limit=4&entity_slug=other:e1&since=2026-09-01",
    )

    assert res.status_code == 200
    body = res.json()
    assert len(body["findings"]) == 4
    assert body["limit"] == 4
    assert body["total"] == 6

    (_, count_params), = driver.count_calls()
    assert count_params["entity_slug"] == "other:e1"
    assert count_params["since"] == "2026-09-01"
