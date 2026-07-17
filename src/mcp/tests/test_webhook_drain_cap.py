# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

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
