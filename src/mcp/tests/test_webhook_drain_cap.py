# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""AF-019 regression: _run_webhook_drain bounds LPOPs across ALL matched
``cerid:webhook_inbox:*`` keys per run, not per-key.

Before the fix ``max_per_run`` was applied to each key independently, so N
inbox keys let one run drain N × max_per_run entries — unbounded in the number
of sources. The bound is now a single global per-run counter.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest


class _FakeRedis:
    def __init__(self, inboxes: dict[str, list[str]]):
        self._store = {k: list(v) for k, v in inboxes.items()}

    def scan_iter(self, match=None, count=None):
        for k in list(self._store.keys()):
            yield k

    def lpop(self, key):
        lst = self._store.get(key)
        if not lst:
            return None
        return lst.pop(0)

    def rpush(self, key, val):
        self._store.setdefault(key, []).append(val)


@pytest.mark.asyncio
async def test_webhook_drain_caps_globally_across_keys(monkeypatch):
    import config
    from app.scheduler import _run_webhook_drain

    entry = json.dumps({"normalized": [{"content": "hello", "title": "t"}]})
    # 4 source inboxes, 3 entries each = 12 available.
    inboxes = {f"cerid:webhook_inbox:s{i}": [entry, entry, entry] for i in range(4)}
    fake = _FakeRedis(inboxes)
    monkeypatch.setattr(config, "WEBHOOK_DRAIN_MAX_PER_RUN", 5, raising=False)

    calls: list[str] = []

    def fake_ingest(content=None, domain=None, metadata=None):
        calls.append(content)
        return {"artifact_id": "a", "quality_score": 0.9}

    with patch("app.scheduler.get_redis", return_value=fake), \
         patch("app.scheduler.get_neo4j", return_value=None), \
         patch("app.services.ingestion.ingest_content", side_effect=fake_ingest):
        await _run_webhook_drain()

    # Global cap = 5: exactly 5 entries drained across all 4 keys
    # (pre-fix this would have been up to 4 × 5 = 20).
    assert len(calls) == 5


@pytest.mark.asyncio
async def test_webhook_drain_processes_entries_concurrently(monkeypatch):
    """AF-019: entries are no longer ingested one-at-a-time.

    Each fake ingest holds its slot briefly and records how many other
    ingests were in flight at the same instant. With sequential processing
    the observed max concurrency would be 1; the bounded-concurrency fix
    must show more than one in-flight call (bounded by config.INGEST_CONCURRENCY,
    fixed at module import time — see app.scheduler._webhook_drain_semaphore).
    """
    import threading
    import time as _time

    import config
    from app.scheduler import _run_webhook_drain

    entry = json.dumps({"normalized": [{"content": "hello", "title": "t"}]})
    inboxes = {"cerid:webhook_inbox:s0": [entry] * 6}
    fake = _FakeRedis(inboxes)
    monkeypatch.setattr(config, "WEBHOOK_DRAIN_MAX_PER_RUN", 6, raising=False)

    lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0

    def fake_ingest(content=None, domain=None, metadata=None):
        nonlocal in_flight, max_in_flight
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        _time.sleep(0.05)
        with lock:
            in_flight -= 1
        return {"artifact_id": "a", "quality_score": 0.9}

    from app.scheduler import _webhook_drain_semaphore

    # The semaphore is created once at module import time from
    # config.INGEST_CONCURRENCY, so its capacity (not the monkeypatched
    # config value) is the real bound for this run.
    concurrency_cap = _webhook_drain_semaphore._value

    with patch("app.scheduler.get_redis", return_value=fake), \
         patch("app.scheduler.get_neo4j", return_value=None), \
         patch("app.services.ingestion.ingest_content", side_effect=fake_ingest):
        await _run_webhook_drain()

    assert max_in_flight > 1, "entries were still processed one at a time"
    assert max_in_flight <= concurrency_cap, "concurrency exceeded the configured bound"


@pytest.mark.asyncio
async def test_webhook_drain_reports_partial_on_mixed_outcomes(monkeypatch):
    """AF-019: a run with any failures is no longer unconditionally 'success'."""
    import config
    from app import scheduler
    from app.scheduler import _run_webhook_drain

    good = json.dumps({"normalized": [{"content": "hello", "title": "t"}]})
    bad = "not-json"
    inboxes = {"cerid:webhook_inbox:s0": [good, bad, good]}
    fake = _FakeRedis(inboxes)
    monkeypatch.setattr(config, "WEBHOOK_DRAIN_MAX_PER_RUN", 10, raising=False)

    logged: list[tuple[str, str]] = []

    def fake_log_execution(job_name, status, duration, detail=""):
        logged.append((job_name, status))

    def fake_ingest(content=None, domain=None, metadata=None):
        return {"artifact_id": "a", "quality_score": 0.9}

    with patch("app.scheduler.get_redis", return_value=fake), \
         patch("app.scheduler.get_neo4j", return_value=None), \
         patch("app.services.ingestion.ingest_content", side_effect=fake_ingest), \
         patch.object(scheduler, "_log_execution", side_effect=fake_log_execution):
        await _run_webhook_drain()

    assert ("webhook_drain", "partial") in logged


@pytest.mark.asyncio
async def test_webhook_drain_reports_error_when_all_entries_fail(monkeypatch):
    """AF-019: a run that drains entries but ingests nothing is not 'success'."""
    import config
    from app import scheduler
    from app.scheduler import _run_webhook_drain

    bad = "not-json"
    inboxes = {"cerid:webhook_inbox:s0": [bad, bad]}
    fake = _FakeRedis(inboxes)
    monkeypatch.setattr(config, "WEBHOOK_DRAIN_MAX_PER_RUN", 10, raising=False)

    logged: list[tuple[str, str]] = []

    def fake_log_execution(job_name, status, duration, detail=""):
        logged.append((job_name, status))

    with patch("app.scheduler.get_redis", return_value=fake), \
         patch("app.scheduler.get_neo4j", return_value=None), \
         patch.object(scheduler, "_log_execution", side_effect=fake_log_execution):
        await _run_webhook_drain()

    assert ("webhook_drain", "error") in logged
