# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Ingest routes must not flush C1 outside the domain they ingested into.

``app/services/ingestion.py`` is the single invalidation contract (audit
CL-14): every ingest funnels through ``ingest_content`` and fires the
domain-scoped ``invalidate_query_caches_threaded(domain=...)`` hook. The
router/job layers must therefore add no invalidation of their own — an
unscoped flush there pins ``cache_hit_rate`` at 0.0 regardless of how well
the service-level hook scopes.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import fakeredis
import httpx
import pytest
from fastapi import FastAPI

from utils.query_cache import get_cached, invalidate_by_domain, set_cached


def _make_app() -> FastAPI:
    from app.routers.ingestion import router

    app = FastAPI()
    app.include_router(router)
    return app


async def _drain_background_tasks() -> None:
    """Let any fire-and-forget ``create_task`` scheduled by the handler run.

    Without this the assertion could pass simply because the flush had not
    been given a chance to execute yet.
    """
    for _ in range(5):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if not pending:
            return
        await asyncio.wait(pending, timeout=2)


@pytest.fixture()
def redis_cache():
    redis = fakeredis.FakeRedis(decode_responses=True)
    with patch("utils.query_cache.get_redis", return_value=redis):
        # Retire the one-time legacy-sweep fallback so a domain-scoped
        # invalidation in this test evicts by index rather than full-flushing.
        invalidate_by_domain("_warmup_")
        yield redis


async def _post(payload: dict, path: str = "/ingest") -> httpx.Response:
    transport = httpx.ASGITransport(app=_make_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(path, json=payload)


async def test_rest_ingest_keeps_cache_entry_for_another_domain(redis_cache):
    """A REST ingest into ``finance`` leaves a C1 entry that searched only
    ``coding`` intact."""
    set_cached("coding q", "coding", 10, {"answer": "c"}, domains_searched=["coding"])

    with patch(
        "app.routers.ingestion.ingest_content",
        return_value={"status": "success", "artifact_id": "art:1", "domain": "finance"},
    ):
        resp = await _post({"content": "quarterly numbers", "domain": "finance"})

    assert resp.status_code == 200
    await _drain_background_tasks()

    kept = get_cached("coding q", "coding", 10)
    assert kept is not None and kept["answer"] == "c"


async def test_ingest_file_keeps_cache_entry_for_another_domain(redis_cache):
    """Same contract on the file route, which flushed synchronously."""
    set_cached("coding q", "coding", 10, {"answer": "c"}, domains_searched=["coding"])

    async def _fake_ingest_file(**_kwargs):
        return {"status": "success", "artifact_id": "art:2", "domain": "finance"}

    with patch("app.routers.ingestion.ingest_file", side_effect=_fake_ingest_file):
        resp = await _post(
            {"file_path": "/archive/finance/report.txt", "domain": "finance"},
            path="/ingest_file",
        )

    assert resp.status_code == 200
    await _drain_background_tasks()

    kept = get_cached("coding q", "coding", 10)
    assert kept is not None and kept["answer"] == "c"
