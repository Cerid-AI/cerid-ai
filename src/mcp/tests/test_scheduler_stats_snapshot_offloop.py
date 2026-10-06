# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The daily stats snapshot must not block the scheduler's event loop.

``_run_knowledge_stats_snapshot`` is ``async`` but called the synchronous
Neo4j reads and the write inline, so every other APScheduler job and the
loop itself waited on two store round-trips once a day.
"""
from __future__ import annotations

import asyncio
import threading

from app import scheduler


def test_the_store_calls_run_off_the_event_loop(monkeypatch):
    import app.db.neo4j.stats as stats
    import app.deps as deps

    threads: dict[str, int] = {}

    def fetch(driver):
        threads["fetch"] = threading.get_ident()
        return {"nodes": {"artifacts": 3}}

    def write(driver, snapshot):
        threads["write"] = threading.get_ident()

    monkeypatch.setattr(deps, "get_neo4j", lambda: object())
    monkeypatch.setattr(stats, "fetch_current_stats", fetch)
    monkeypatch.setattr(stats, "write_stats_snapshot", write)
    logged: list[tuple] = []
    monkeypatch.setattr(scheduler, "_log_execution", lambda *a, **k: logged.append(a))

    loop_thread = threading.get_ident()
    asyncio.run(scheduler._run_knowledge_stats_snapshot())

    assert threads.keys() == {"fetch", "write"}
    assert threads["fetch"] != loop_thread
    assert threads["write"] != loop_thread
    assert logged and logged[0][:2] == ("knowledge_stats_snapshot", "success")
