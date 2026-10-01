# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""A daily digest that was not saved is not announced as ready.

The scheduled job logged "success" and fired ``digest.ready`` whenever the
digest was not skipped, without looking at whether the save returned an
artifact id. A failed save therefore announced a digest nobody could open.

The job and the digest agent both run for real. The fakes sit at the edges:
the graph accessor, the model call, the HTTP client used for the save, the
webhook sender and Redis.
"""
from __future__ import annotations

import json
from typing import Any

import pytest


class _FakePipeline:
    def __init__(self, pushed: list[dict[str, Any]]) -> None:
        self._pushed = pushed

    def lpush(self, _key: str, payload: str) -> None:
        self._pushed.append(json.loads(payload))

    def ltrim(self, *_a: Any) -> None:
        return None

    def expire(self, *_a: Any) -> None:
        return None

    def execute(self) -> None:
        return None


class _FakeRedis:
    def __init__(self) -> None:
        self.pushed: list[dict[str, Any]] = []

    def get(self, _key: str) -> None:
        return None

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self.pushed)


class _FakeGraph:
    def get_driver(self) -> object:
        return object()

    def list_artifacts(self, _driver: Any, **_kw: Any) -> list[dict[str, Any]]:
        return [{"domain": "notes", "filename": "n.md", "summary": "s"}]


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


def _http_client_returning(response: _FakeResponse) -> type:
    class _FakeAsyncClient:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def post(self, *_a: Any, **_kw: Any) -> _FakeResponse:
            return response

    return _FakeAsyncClient


@pytest.fixture
def digest_env(monkeypatch):
    import app.deps as deps
    import config.features as features
    import core.agents.daily_digest as digest
    import core.utils.internal_llm as internal_llm
    import utils.webhooks as webhooks
    from app import scheduler as sched

    redis = _FakeRedis()
    fired: list[tuple[str, dict[str, Any]]] = []

    async def _llm(*_a: Any, **_kw: Any) -> str:
        return '{"key_threads": [{"title": "Note", "body": "a note"}]}'

    async def _fire_event(event_type: str, payload: dict[str, Any]) -> int:
        fired.append((event_type, payload))
        return 1

    monkeypatch.setenv("CERID_DAILY_DIGEST_ENABLED", "true")
    monkeypatch.setattr(features, "is_feature_enabled", lambda _name: True)
    monkeypatch.setattr(deps, "get_redis", lambda: redis)
    monkeypatch.setattr(sched, "get_redis", lambda: redis)
    monkeypatch.setattr(digest, "_graph", _FakeGraph())
    monkeypatch.setattr(internal_llm, "call_internal_llm", _llm)
    monkeypatch.setattr(webhooks, "fire_event", _fire_event)
    return redis, fired


def _digest_runs(redis: _FakeRedis) -> list[dict[str, Any]]:
    return [e for e in redis.pushed if e.get("job") == "daily_digest"]


@pytest.mark.asyncio
async def test_failed_save_is_an_error_and_fires_no_ready_event(digest_env, monkeypatch):
    import httpx

    from app.scheduler import _run_daily_digest

    redis, fired = digest_env
    monkeypatch.setattr(
        httpx, "AsyncClient", _http_client_returning(_FakeResponse(500, {})),
    )

    await _run_daily_digest()

    assert fired == []
    runs = _digest_runs(redis)
    assert [r["status"] for r in runs] == ["error"]


@pytest.mark.asyncio
async def test_saved_digest_is_a_success_and_fires_ready(digest_env, monkeypatch):
    import httpx

    from app.scheduler import _run_daily_digest

    redis, fired = digest_env
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        _http_client_returning(_FakeResponse(200, {"artifact_id": "art:1"})),
    )

    await _run_daily_digest()

    assert [name for name, _ in fired] == ["digest.ready"]
    assert fired[0][1]["persisted_artifact_id"] == "art:1"
    runs = _digest_runs(redis)
    assert [r["status"] for r in runs] == ["success"]
