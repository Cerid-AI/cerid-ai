# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Scheduled-job runs do not push ingest events out of the audit log.

Every scheduled-job run was appended to the same capped Redis list as
ingest, query and rectify events. Jobs that run every minute or two filled
the cap in days, so the readers of that list (activity summary, ingest
stats, /ingest_log) saw mostly job rows and lost the events they report on.

Only Redis is faked (fakeredis); the writer, the reader and the route run
for real.
"""
from __future__ import annotations

import json

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def redis(monkeypatch):
    from app import scheduler as sched
    from app.routers import health

    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(sched, "get_redis", lambda: fake)
    monkeypatch.setattr(health, "get_redis", lambda: fake)
    return fake


@pytest.fixture
def client(redis):
    from app.routers.health import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_job_runs_do_not_evict_ingest_events(redis, monkeypatch):
    from app import scheduler as sched
    from core.utils.cache import get_log, log_event

    monkeypatch.setattr(sched.config, "REDIS_LOG_MAX", 5)

    log_event(redis, event_type="ingest", artifact_id="a1", domain="notes", filename="kept.md")
    for _ in range(20):
        sched._log_execution("ingest_recovery", "success", 0.1)

    events = get_log(redis, limit=500)
    assert [e["event"] for e in events] == ["ingest"]
    assert events[0]["filename"] == "kept.md"


def test_job_runs_are_still_recorded_and_capped(redis, monkeypatch):
    from app import scheduler as sched

    monkeypatch.setattr(sched.config, "REDIS_LOG_MAX", 5)

    for i in range(8):
        sched._log_execution("webhook_drain", "success", 0.1, f"run {i}")

    job_keys = [k for k in redis.keys("*") if k != sched.config.REDIS_INGEST_LOG]
    assert len(job_keys) == 1
    runs = [json.loads(r) for r in redis.lrange(job_keys[0], 0, -1)]
    assert [r["detail"] for r in runs] == ["run 7", "run 6", "run 5", "run 4", "run 3"]
    assert {r["job"] for r in runs} == {"webhook_drain"}
    assert {r["event"] for r in runs} == {"scheduled_job"}


def test_the_job_log_is_served_newest_first_in_pages(client):
    from app import scheduler as sched

    for i in range(5):
        sched._log_execution("rectify", "success", 0.1, f"run {i}")

    first = client.get("/scheduler/log", params={"limit": 2})
    assert first.status_code == 200
    assert [r["detail"] for r in first.json()] == ["run 4", "run 3"]
    assert first.headers["X-Total-Count"] == "5"
    assert first.headers["X-Has-More"] == "true"

    last = client.get("/scheduler/log", params={"limit": 2, "offset": 4})
    assert [r["detail"] for r in last.json()] == ["run 0"]
    assert last.headers["X-Total-Count"] == "5"
    assert last.headers["X-Has-More"] == "false"


def test_the_job_log_does_not_serve_ingest_events(client, redis):
    from app import scheduler as sched
    from core.utils.cache import log_event

    log_event(redis, event_type="ingest", artifact_id="a1", domain="notes", filename="kept.md")
    sched._log_execution("rectify", "success", 0.1)

    rows = client.get("/scheduler/log").json()
    assert [r["event"] for r in rows] == ["scheduled_job"]


def test_an_unreadable_job_log_is_an_error_not_an_empty_list(client, monkeypatch):
    from app.routers import health

    def _down():
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(health, "get_redis", _down)

    assert client.get("/scheduler/log").status_code == 500
