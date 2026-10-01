# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""``POST /memories/dedup`` reports the supersessions the graph applied.

The endpoint counted every call to ``mark_superseded`` as one memory
superseded. The write can match nothing (a memory deleted between the scan and
the write) or fail outright, and the response still claimed it had landed.

Only the Neo4j driver is faked here; ``mark_superseded`` runs for real.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

_NEW = {
    "id": "mem-new",
    "text": "SOL price is down 1.23% today",
    "created_at": "2026-08-12T10:00:00+00:00",
}
_OLD = {
    "id": "mem-old",
    "text": "SOL price down 1.38%",
    "created_at": "2026-08-12T09:00:00+00:00",
}


class _Result:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, driver: "_Driver") -> None:
        self._driver = driver

    def __enter__(self) -> "_Session":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def run(self, cypher: str, **params):
        if "SUPERSEDES" in cypher:
            self._driver.writes.append(params)
            if self._driver.write_error is not None:
                raise self._driver.write_error
            return _Result([{"n": self._driver.rows_matched}])
        return _Result([_NEW, _OLD])


class _Driver:
    def __init__(self, rows_matched: int, write_error: Exception | None = None) -> None:
        self.rows_matched = rows_matched
        self.write_error = write_error
        self.writes: list[dict] = []

    def session(self) -> _Session:
        return _Session(self)


def _dedup(monkeypatch: pytest.MonkeyPatch, driver: _Driver) -> dict:
    from app.main import app

    monkeypatch.setattr("app.routers.memories.get_neo4j", lambda: driver)
    res = TestClient(app, raise_server_exceptions=False).post(
        "/memories/dedup", json={"confirm": True},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_a_supersession_that_matched_nothing_is_not_counted(monkeypatch):
    driver = _Driver(rows_matched=0)
    data = _dedup(monkeypatch, driver)

    assert len(driver.writes) == 1, "the write was never attempted"
    assert data["duplicate_groups"] == 1
    assert data["memories_superseded"] == 0


def test_a_supersession_that_failed_is_not_counted(monkeypatch):
    driver = _Driver(rows_matched=1, write_error=RuntimeError("graph unavailable"))
    data = _dedup(monkeypatch, driver)

    assert len(driver.writes) == 1
    assert data["memories_superseded"] == 0


def test_an_applied_supersession_is_counted(monkeypatch):
    driver = _Driver(rows_matched=1)
    data = _dedup(monkeypatch, driver)

    assert driver.writes[0]["old_id"] == "mem-old"
    assert driver.writes[0]["new_id"] == "mem-new"
    assert data["memories_superseded"] == 1
