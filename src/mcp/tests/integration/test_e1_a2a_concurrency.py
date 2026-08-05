# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 Phase-1g verifiability harness — A2A CONCURRENCY / LIVENESS probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 1.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-091, CR-034).

The A2A skill executors diverged from the REST paths on concurrency + liveness:

- **CR-091** (high) — ``_execute_query`` calls the retrieval pipeline without the
  ``KB_POOL`` acquisition the REST ``/agent/query`` wraps it in (agents.py), so
  unbounded concurrent A2A retrieval can starve ``/health`` + ``/observability``.
  Worse, ``_execute_ingest`` calls the BLOCKING ``ingest_content`` as a bare
  synchronous call inside an async executor — no ``asyncio.to_thread`` — so a
  single A2A document-ingest blocks the whole event loop for the full
  embed/chunk/Neo4j-write duration (the REST ``/ingest`` path offloads it).

- **CR-034** (medium, the version half) — the agent card hardcodes version
  ``"2.0.0"`` contra the ``pyproject`` single-source ``get_version()``.

Synthetic probes — no live stack. RED-then-GREEN; GREEN → preservation gates.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import fakeredis
import pytest


class _AsyncSpy:
    def __init__(self, ret):
        self._ret = ret
        self.calls = 0

    async def __call__(self, *args, **kwargs):
        self.calls += 1
        return self._ret


class _FakePool:
    """Records KB_POOL.acquire() usage; acquire() is an async context manager."""

    def __init__(self):
        self.acquired = 0

    def acquire(self):
        outer = self

        class _CM:
            async def __aenter__(self):
                outer.acquired += 1
                return None

            async def __aexit__(self, *exc):
                return False

        return _CM()


def _neutral(monkeypatch):
    fr = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fr)
    for g in ("get_chroma", "get_redis", "get_neo4j", "get_graph_store"):
        monkeypatch.setattr(f"app.routers.a2a.{g}", lambda: MagicMock(), raising=False)


# ---------------------------------------------------------------------------
# CR-091 — A2A query must acquire KB_POOL (starvation guard).
# ---------------------------------------------------------------------------

@pytest.mark.preservation
async def test_a2a_query_acquires_kb_pool(monkeypatch):
    """A2A knowledge-query must run under KB_POOL, like REST /agent/query, so
    unbounded A2A retrieval cannot starve the lightweight health routes. RED on
    HEAD: _execute_query never acquires the pool (CR-091)."""
    _neutral(monkeypatch)
    pool = _FakePool()
    monkeypatch.setattr("app.concurrency.KB_POOL", pool)
    seam = _AsyncSpy({"context": "", "sources": [], "results": []})
    monkeypatch.setattr("core.agents.guarded_retrieval.guarded_agent_query_full", seam)

    from app.routers.a2a import _execute_query
    await _execute_query({"query": "who owns the trading book?"})

    assert seam.calls == 1, "A2A query never reached the guarded seam"
    assert pool.acquired == 1, (
        "A2A knowledge-query did not acquire KB_POOL — unbounded A2A retrieval "
        "can starve /health + /observability (CR-091)"
    )


# ---------------------------------------------------------------------------
# CR-091 — A2A ingest must NOT block the event loop.
# ---------------------------------------------------------------------------

@pytest.mark.preservation
async def test_a2a_ingest_runs_off_the_event_loop(monkeypatch):
    """A2A document-ingest must offload the blocking ingest_content to a thread
    so a large document never stalls the loop. RED on HEAD: ingest_content is
    called synchronously on the loop thread (CR-091)."""
    seen: dict = {}

    def _fake_ingest(content, domain="general", metadata=None, *a, **k):
        seen["thread"] = threading.current_thread()
        return {"status": "ingested", "id": "x"}

    monkeypatch.setattr("app.services.ingestion.ingest_content", _fake_ingest)

    from app.routers.a2a import _execute_ingest
    loop_thread = threading.current_thread()
    await _execute_ingest({"text": "a large document body", "domain": "general"})

    assert seen.get("thread") is not None, "ingest_content was never called"
    assert seen["thread"] is not loop_thread, (
        "A2A ingest ran ingest_content on the event-loop thread — a large "
        "document blocks the whole loop until embed/chunk/write completes (CR-091)"
    )


# ---------------------------------------------------------------------------
# CR-034 — agent card version must come from pyproject, not a hardcode.
# ---------------------------------------------------------------------------

@pytest.mark.preservation
async def test_agent_card_version_from_pyproject():
    """The A2A agent card version must track the single-source get_version(),
    not a hardcoded string. RED on HEAD: it is pinned to '2.0.0' (CR-034)."""
    from app.routers.a2a import agent_card
    from core.utils.version import get_version

    card = await agent_card()
    assert card["version"] == get_version(), (
        f"agent card version is hardcoded ({card['version']!r}) and drifts from "
        f"pyproject ({get_version()!r}) (CR-034)"
    )
