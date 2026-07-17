# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the HyPE backfill job + admin endpoint — AF-049.

HyPE indexing ran only at ingest, so enabling ``RETRIEVAL_HYPE_ENABLED`` only
ever covered new chunks. ``HypeBackfillJob`` pages each domain's collection and
indexes existing chunks that have no companion HyPE entry yet.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config
from core.retrieval.hype_index import hype_collection_name

_DOMAIN = "general"
_BASE = config.collection_name(_DOMAIN)
_HYPE = hype_collection_name(_BASE)


def _collection(ids, *, docs=None, metas=None) -> MagicMock:
    coll = MagicMock()
    payload: dict = {"ids": ids}
    if docs is not None:
        payload["documents"] = docs
    if metas is not None:
        payload["metadatas"] = metas
    coll.get.return_value = payload
    return coll


def _chroma(collections: dict) -> MagicMock:
    chroma = MagicMock()

    def _get(name):
        if name in collections:
            return collections[name]
        raise ValueError(f"no collection {name}")

    chroma.get_collection.side_effect = _get
    return chroma


async def _noop(_pct):
    return None


@pytest.mark.asyncio
async def test_no_op_when_flag_off(monkeypatch):
    from app.processor.jobs.hype_backfill import HypeBackfillJob

    monkeypatch.delenv("RETRIEVAL_HYPE_ENABLED", raising=False)
    with patch(
        "app.services.hype_indexer.index_chunk_with_hype", new_callable=AsyncMock
    ) as mock_idx:
        result = await HypeBackfillJob(domain=_DOMAIN).run(_noop)

    assert result.metadata["enabled"] is False
    assert result.metadata["indexed"] == 0
    mock_idx.assert_not_called()


@pytest.mark.asyncio
async def test_indexes_unindexed_skips_indexed_and_empty(monkeypatch):
    from app.processor.jobs.hype_backfill import HypeBackfillJob

    monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")
    base = _collection(
        ids=["c1", "c2", "c3", "c4"],
        docs=["alpha text", "beta text", "   ", "delta text"],
        metas=[{"artifact_id": "a1"}, {"artifact_id": "a2"},
               {"artifact_id": "a3"}, {"artifact_id": "a4"}],
    )
    hype = _collection(ids=["c2_hype_0"], metas=[{"source_chunk_id": "c2"}])
    chroma = _chroma({_BASE: base, _HYPE: hype})

    with (
        patch("app.deps.get_chroma", return_value=chroma),
        patch("app.deps.get_redis", return_value=MagicMock()),
        patch("app.services.hype_indexer.index_chunk_with_hype",
              new_callable=AsyncMock, return_value={"enabled": True, "n_prompts": 5}) as mock_idx,
        patch("utils.query_cache.invalidate_query_caches_non_blocking",
              new_callable=AsyncMock),
    ):
        result = await HypeBackfillJob(domain=_DOMAIN).run(_noop)

    m = result.metadata
    assert m["indexed"] == 2      # c1, c4
    assert m["skipped"] == 1      # c2 already indexed
    assert m["empty"] == 1        # c3 blank text
    assert m["scanned"] == 4
    assert m["capped"] is False
    indexed_ids = {call.args[0] for call in mock_idx.await_args_list}
    assert indexed_ids == {"c1", "c4"}
    # Provenance threaded through from base-chunk metadata.
    a4_call = next(c for c in mock_idx.await_args_list if c.args[0] == "c4")
    assert a4_call.kwargs["artifact_id"] == "a4"
    assert a4_call.kwargs["collection_name"] == _BASE


@pytest.mark.asyncio
async def test_respects_max_chunks_cap(monkeypatch):
    from app.processor.jobs.hype_backfill import HypeBackfillJob

    monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")
    base = _collection(
        ids=["c1", "c2", "c3"],
        docs=["a", "b", "c"],
        metas=[{"artifact_id": "a1"}, {"artifact_id": "a2"}, {"artifact_id": "a3"}],
    )
    chroma = _chroma({_BASE: base})  # no hype collection yet → all un-indexed

    with (
        patch("app.deps.get_chroma", return_value=chroma),
        patch("app.deps.get_redis", return_value=MagicMock()),
        patch("app.services.hype_indexer.index_chunk_with_hype",
              new_callable=AsyncMock, return_value={"enabled": True, "n_prompts": 5}),
        patch("utils.query_cache.invalidate_query_caches_non_blocking",
              new_callable=AsyncMock),
    ):
        result = await HypeBackfillJob(domain=_DOMAIN, max_chunks=2).run(_noop)

    assert result.metadata["indexed"] == 2
    assert result.metadata["capped"] is True


@pytest.mark.asyncio
async def test_force_reindexes_already_indexed(monkeypatch):
    from app.processor.jobs.hype_backfill import HypeBackfillJob

    monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")
    base = _collection(
        ids=["c1", "c2"], docs=["a", "b"],
        metas=[{"artifact_id": "a1"}, {"artifact_id": "a2"}],
    )
    hype = _collection(
        ids=["c1_hype_0", "c2_hype_0"],
        metas=[{"source_chunk_id": "c1"}, {"source_chunk_id": "c2"}],
    )
    chroma = _chroma({_BASE: base, _HYPE: hype})

    with (
        patch("app.deps.get_chroma", return_value=chroma),
        patch("app.deps.get_redis", return_value=MagicMock()),
        patch("app.services.hype_indexer.index_chunk_with_hype",
              new_callable=AsyncMock, return_value={"enabled": True, "n_prompts": 5}) as mock_idx,
        patch("utils.query_cache.invalidate_query_caches_non_blocking",
              new_callable=AsyncMock),
    ):
        result = await HypeBackfillJob(domain=_DOMAIN, force=True).run(_noop)

    assert result.metadata["indexed"] == 2  # skip-set bypassed by force
    assert result.metadata["skipped"] == 0
    assert mock_idx.await_count == 2


@pytest.mark.asyncio
async def test_swallows_per_chunk_error(monkeypatch):
    from app.processor.jobs.hype_backfill import HypeBackfillJob

    monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")
    base = _collection(
        ids=["c1", "c2"], docs=["a", "b"],
        metas=[{"artifact_id": "a1"}, {"artifact_id": "a2"}],
    )
    chroma = _chroma({_BASE: base})

    async def _idx(cid, *a, **k):
        if cid == "c1":
            raise RuntimeError("llm down")
        return {"enabled": True, "n_prompts": 5}

    with (
        patch("app.deps.get_chroma", return_value=chroma),
        patch("app.deps.get_redis", return_value=MagicMock()),
        patch("app.services.hype_indexer.index_chunk_with_hype", side_effect=_idx),
        patch("utils.query_cache.invalidate_query_caches_non_blocking",
              new_callable=AsyncMock),
    ):
        result = await HypeBackfillJob(domain=_DOMAIN).run(_noop)  # must not raise

    assert result.metadata["errors"] == 1   # c1
    assert result.metadata["indexed"] == 1   # c2 still processed


@pytest.mark.asyncio
async def test_busts_query_cache_when_indexed(monkeypatch):
    from app.processor.jobs.hype_backfill import HypeBackfillJob

    monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")
    base = _collection(ids=["c1"], docs=["a"], metas=[{"artifact_id": "a1"}])
    chroma = _chroma({_BASE: base})

    with (
        patch("app.deps.get_chroma", return_value=chroma),
        patch("app.deps.get_redis", return_value=MagicMock()),
        patch("app.services.hype_indexer.index_chunk_with_hype",
              new_callable=AsyncMock, return_value={"enabled": True, "n_prompts": 5}),
        patch("utils.query_cache.invalidate_query_caches_non_blocking",
              new_callable=AsyncMock) as mock_bust,
    ):
        await HypeBackfillJob(domain=_DOMAIN).run(_noop)

    mock_bust.assert_awaited_once()


# ---------------------------------------------------------------------------
# Admin endpoint
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_endpoint_enqueues(monkeypatch):
    from app.routers.kb_admin import HypeBackfillRequest, hype_backfill_corpus

    queue = MagicMock()
    queue.enqueue_if_absent = AsyncMock(return_value="job-123")
    with (
        patch("app.routers.kb_admin.get_redis", return_value=MagicMock()),
        patch("app.db.redis.processor_queue.RedisJobQueue", return_value=queue),
    ):
        resp = await hype_backfill_corpus(HypeBackfillRequest(domain=None, force=False))

    assert resp.status == "enqueued"
    assert resp.job_id == "job-123"


@pytest.mark.asyncio
async def test_endpoint_404_unknown_domain():
    from fastapi import HTTPException

    from app.routers.kb_admin import HypeBackfillRequest, hype_backfill_corpus

    with pytest.raises(HTTPException) as ei:
        await hype_backfill_corpus(HypeBackfillRequest(domain="does-not-exist"))
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_endpoint_already_running(monkeypatch):
    from app.routers.kb_admin import HypeBackfillRequest, hype_backfill_corpus

    queue = MagicMock()
    queue.enqueue_if_absent = AsyncMock(return_value=None)  # duplicate collapsed
    with (
        patch("app.routers.kb_admin.get_redis", return_value=MagicMock()),
        patch("app.db.redis.processor_queue.RedisJobQueue", return_value=queue),
    ):
        resp = await hype_backfill_corpus(HypeBackfillRequest(domain=None))

    assert resp.status == "already_running"
    assert resp.job_id is None
