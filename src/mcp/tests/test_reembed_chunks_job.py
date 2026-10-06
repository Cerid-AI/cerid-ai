# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for app/processor/jobs/reembed_chunks.py (RAG Quality Program Phase 4.4).

Covers:
- estimate_cost(): zero-token CPU job, matching compute_entity_embeddings'
  non-LLM CostEstimate shape.
- _reembed_domain(): stale-stamp detection keyed on the serving artifact
  (``serving_embedding_version``), force=True re-embeds everything,
  collection.update() called with NO ``embeddings=`` kwarg (lets ChromaDB
  recompute via the bound embedder), both stamp fields written from the
  serving functions — never ``config.EMBEDDING_MODEL``, which is how the
  job used to recreate the mislabel it was meant to fix.
  AF-037: a page-read failure logs, skips past the failed offset, and is
  reported via ``failed_offsets`` instead of silently truncating the scan.
- restamp_only: rewrites the two stamp fields on chunks whose vectors already
  sit in the serving space (the boot probe's check) without re-embedding;
  dry_run reports counts per current stamp value and writes nothing.
- run(): iterates config.DOMAINS when domain=None, a single domain when
  given; semantic-cache invalidation fires only when something was
  actually re-embedded; JobResult.metadata shape, including the AF-037
  ``truncated``/``truncated_domains`` failure signal.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import numpy as np

import config as cfg
from app.processor.jobs import reembed_chunks
from app.processor.jobs.reembed_chunks import DomainOutcome, ReembedChunksJob
from core.utils import embeddings as emb
from tests.helpers.fake_chroma import FakeChromaClient, FakeChromaCollection

SERVING_MODEL = emb.serving_embedding_model()
SERVING_VERSION = emb.serving_embedding_version()
OLD_MODEL = "Snowflake/snowflake-arctic-embed-m-v1.5"
OLD_VERSION = "Snowflake/snowflake-arctic-embed-m-v1.5"


def _make_job(**kwargs):
    return ReembedChunksJob(**kwargs)


def _run(job, chroma, domain="coding") -> DomainOutcome:
    return asyncio.run(job._reembed_domain(chroma, domain))


# Deterministic "embedder": a unit vector per distinct text, so a stored
# vector equal to fake_embed(text) is "in the serving space" and anything
# orthogonal is not — the same geometry the boot probe keys on.
_AXES: dict[str, int] = {}


def fake_embed(texts: list[str]) -> list[list[float]]:
    out = []
    for t in texts:
        axis = _AXES.setdefault(t, len(_AXES))
        v = [0.0] * 16
        v[axis % 15] = 1.0
        out.append(v)
    return out


def _other_space(_: str) -> list[float]:
    v = [0.0] * 16
    v[15] = 1.0
    return v


def _mixed_collection() -> FakeChromaCollection:
    """Four chunks: one already stamped with the serving artifact, one
    mislabeled Snowflake (vector really in the serving space), one unstamped
    legacy chunk (also in the serving space), one whose vector is from another
    model entirely."""
    coll = FakeChromaCollection(cfg.collection_name("coding"), embedding_function=fake_embed)
    coll.upsert(
        ids=["current", "mislabeled", "legacy", "foreign"],
        documents=["doc current", "doc mislabeled", "doc legacy", "doc foreign"],
        embeddings=[
            fake_embed(["doc current"])[0],
            fake_embed(["doc mislabeled"])[0],
            fake_embed(["doc legacy"])[0],
            _other_space("doc foreign"),
        ],
        metadatas=[
            {"embedding_model": SERVING_MODEL, "embedding_model_version": SERVING_VERSION},
            {"embedding_model": OLD_MODEL, "embedding_model_version": OLD_VERSION},
            {},
            {"embedding_model": OLD_MODEL, "embedding_model_version": OLD_VERSION},
        ],
    )
    return coll


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------


class TestEstimateCost:
    def test_zero_token_cpu_job(self):
        job = _make_job()
        estimate = job.estimate_cost()
        assert estimate.estimated_tokens_in == 0
        assert estimate.estimated_tokens_out == 0
        assert estimate.model == "cpu/embeddings"
        assert estimate.estimated_usd == Decimal("0.00")


# ---------------------------------------------------------------------------
# _reembed_domain — staleness keyed on the serving artifact
# ---------------------------------------------------------------------------


class TestSelectionAgainstMixedStamps:
    def test_only_chunks_not_stamped_with_the_serving_artifact_are_reembedded(self):
        coll = _mixed_collection()
        chroma = FakeChromaClient([coll])
        before_current = coll.embedding_of("current")

        out = _run(_make_job(domain="coding"), chroma)

        assert out.processed == 4
        assert out.skipped == 1
        assert out.reembedded == 3
        assert out.failed_offsets == []
        # The current chunk was not rewritten; the others were re-embedded by
        # the collection's bound embedder (update without embeddings=).
        assert np.array_equal(coll.embedding_of("current"), before_current)
        assert np.array_equal(coll.embedding_of("foreign"), fake_embed(["doc foreign"])[0])
        for cid in ("mislabeled", "legacy", "foreign"):
            meta = coll.metadata_of(cid)
            assert meta["embedding_model"] == SERVING_MODEL
            assert meta["embedding_model_version"] == SERVING_VERSION

    def test_writers_name_the_serving_leg_not_the_onnx_pin(self, monkeypatch):
        """Live shape: the local server embeds nomic while EMBEDDING_MODEL still
        names the Snowflake ONNX pin. The old writer stamped config.EMBEDDING_MODEL."""
        monkeypatch.setenv("EMBEDDINGS_PROVIDER", "quenchforge")
        monkeypatch.setenv("QUENCHFORGE_EMBED_MODEL", "nomic-embed-text-v1.5")
        monkeypatch.setenv("QUENCHFORGE_URL", "http://embed-server.test:11434")
        monkeypatch.setattr(emb.config, "EMBEDDING_MODEL", OLD_MODEL)
        emb._reset_serving_version_cache_for_testing()
        monkeypatch.setattr(
            httpx, "get",
            lambda url, **_: httpx.Response(
                200, json={"version": "cerid-mlx-7"}, request=httpx.Request("GET", url),
            ),
        )
        coll = FakeChromaCollection(cfg.collection_name("coding"), embedding_function=fake_embed)
        coll.upsert(ids=["c1"], documents=["doc one"], metadatas=[{}])

        out = _run(_make_job(domain="coding"), FakeChromaClient([coll]))

        assert out.reembedded == 1
        assert coll.metadata_of("c1") == {
            "embedding_model": "nomic-embed-text-v1.5",
            "embedding_model_version": "nomic-embed-text-v1.5@cerid-mlx-7",
        }
        emb._reset_serving_version_cache_for_testing()

    def test_force_reembeds_even_matching_stamp(self):
        collection = MagicMock()
        collection.get.return_value = {
            "ids": ["c1"],
            "documents": ["doc1"],
            "metadatas": [{"embedding_model_version": SERVING_VERSION}],
        }
        chroma = MagicMock()
        chroma.get_collection.return_value = collection

        out = _run(_make_job(domain="coding", force=True), chroma)

        assert (out.processed, out.reembedded, out.skipped, out.failed_offsets) == (1, 1, 0, [])
        collection.update.assert_called_once()
        assert "embeddings" not in collection.update.call_args.kwargs

    def test_no_stale_chunks_skips_update_call(self):
        collection = MagicMock()
        collection.get.return_value = {
            "ids": ["c1"],
            "documents": ["doc1"],
            "metadatas": [{"embedding_model_version": SERVING_VERSION}],
        }
        chroma = MagicMock()
        chroma.get_collection.return_value = collection

        out = _run(_make_job(domain="coding"), chroma)

        assert (out.processed, out.reembedded, out.skipped) == (1, 0, 1)
        collection.update.assert_not_called()

    def test_missing_collection_returns_zero_without_raising(self):
        chroma = MagicMock()
        chroma.get_collection.side_effect = Exception("collection not found")

        out = _run(_make_job(domain="ghost-domain"), chroma, "ghost-domain")

        assert (out.processed, out.reembedded, out.skipped, out.failed_offsets) == (0, 0, 0, [])

    def test_pagination_across_two_batches(self):
        """batch_size=1 forces two collection.get() calls before exhaustion."""
        collection = MagicMock()
        collection.get.side_effect = [
            {"ids": ["c1"], "documents": ["d1"], "metadatas": [{}]},
            {"ids": ["c2"], "documents": ["d2"], "metadatas": [{}]},
            {"ids": [], "documents": [], "metadatas": []},
        ]
        chroma = MagicMock()
        chroma.get_collection.return_value = collection

        out = _run(_make_job(domain="coding", batch_size=1), chroma)

        assert (out.processed, out.reembedded, out.failed_offsets) == (2, 2, [])
        assert collection.get.call_count == 3

    def test_one_bad_page_does_not_abort_the_scan(self):
        """AF-037: a transient failure on one page must not truncate the
        domain scan — the loop advances past the failed offset and keeps
        reading subsequent pages, reporting the failure instead of hiding it."""
        collection = MagicMock()
        collection.get.side_effect = [
            {"ids": ["c1"], "documents": ["d1"], "metadatas": [{}]},
            RuntimeError("transient chroma read error"),
            {"ids": ["c3"], "documents": ["d3"], "metadatas": [{}]},
            {"ids": [], "documents": [], "metadatas": []},
        ]
        chroma = MagicMock()
        chroma.get_collection.return_value = collection

        with patch("core.utils.swallowed.log_swallowed_error"):
            out = _run(_make_job(domain="coding", batch_size=1), chroma)

        assert (out.processed, out.reembedded, out.failed_offsets) == (2, 2, [1])

    def test_every_page_failing_aborts_after_max_consecutive_failures(self):
        """A collection that fails on EVERY page must not spin forever."""
        from app.processor.jobs.reembed_chunks import _MAX_CONSECUTIVE_BATCH_FAILURES

        collection = MagicMock()
        collection.get.side_effect = RuntimeError("collection unreachable")
        chroma = MagicMock()
        chroma.get_collection.return_value = collection

        with patch("core.utils.swallowed.log_swallowed_error"):
            out = _run(_make_job(domain="coding", batch_size=1), chroma)

        assert (out.processed, out.reembedded) == (0, 0)
        assert len(out.failed_offsets) == _MAX_CONSECUTIVE_BATCH_FAILURES
        assert collection.get.call_count == _MAX_CONSECUTIVE_BATCH_FAILURES


# ---------------------------------------------------------------------------
# restamp_only — rewrite the stamp where the vector is already in the space
# ---------------------------------------------------------------------------


class TestRestampOnly:
    def test_dry_run_counts_per_current_stamp_and_writes_nothing(self, monkeypatch):
        monkeypatch.setattr(reembed_chunks, "get_embedding_function", lambda: fake_embed)
        coll = _mixed_collection()
        snapshot = {cid: (coll.embedding_of(cid), coll.metadata_of(cid))
                    for cid in ("current", "mislabeled", "legacy", "foreign")}

        out = _run(_make_job(domain="coding", restamp_only=True, dry_run=True), FakeChromaClient([coll]))

        assert out.processed == 4
        assert out.skipped == 1
        assert out.reembedded == 0
        assert out.restamped == 0
        assert out.in_serving_space == 2
        assert out.out_of_serving_space == 1
        assert out.by_stamp == {
            f"{OLD_MODEL} / {OLD_VERSION}": {"in_serving_space": 1, "out_of_serving_space": 1},
            "unstamped / unstamped": {"in_serving_space": 1, "out_of_serving_space": 0},
        }
        for cid, (vec, meta) in snapshot.items():
            assert np.array_equal(coll.embedding_of(cid), vec)
            assert coll.metadata_of(cid) == meta

    def test_apply_restamps_in_space_chunks_without_touching_vectors(self, monkeypatch):
        monkeypatch.setattr(reembed_chunks, "get_embedding_function", lambda: fake_embed)
        coll = _mixed_collection()
        vectors = {cid: coll.embedding_of(cid) for cid in ("current", "mislabeled", "legacy", "foreign")}

        out = _run(_make_job(domain="coding", restamp_only=True), FakeChromaClient([coll]))

        assert out.restamped == 2
        assert out.reembedded == 0
        assert out.out_of_serving_space == 1
        for cid in ("mislabeled", "legacy"):
            assert coll.metadata_of(cid) == {
                "embedding_model": SERVING_MODEL,
                "embedding_model_version": SERVING_VERSION,
            }
        # The foreign-space chunk keeps its (truthful) old stamp: a restamp
        # must never claim a vector it cannot reproduce.
        assert coll.metadata_of("foreign")["embedding_model"] == OLD_MODEL
        for cid, vec in vectors.items():
            assert np.array_equal(coll.embedding_of(cid), vec)

    def test_restamp_without_an_in_process_embedder_verifies_nothing(self, monkeypatch):
        """Store-side embedding (ChromaDB default model): there is no embedder
        here to reproduce a vector with, so nothing can be shown in-space."""
        monkeypatch.setattr(reembed_chunks, "get_embedding_function", lambda: None)
        coll = _mixed_collection()

        out = _run(_make_job(domain="coding", restamp_only=True), FakeChromaClient([coll]))

        assert out.restamped == 0
        assert out.in_serving_space == 0
        assert out.out_of_serving_space == 3
        assert coll.metadata_of("legacy") == {}


# ---------------------------------------------------------------------------
# run() — orchestration across domains + cache invalidation
# ---------------------------------------------------------------------------


def _outcome(**kw) -> DomainOutcome:
    out = DomainOutcome()
    for k, v in kw.items():
        setattr(out, k, v)
    return out


class TestRunMethod:
    def test_run_scopes_to_single_domain(self):
        job = _make_job(domain="coding")

        async def _test():
            progress_calls: list[float] = []

            async def capture_progress(pct: float) -> None:
                progress_calls.append(pct)

            with (
                patch("app.deps.get_chroma", return_value=MagicMock()),
                patch("app.deps.get_redis", return_value=MagicMock()),
                patch.object(
                    job, "_reembed_domain",
                    return_value=_outcome(processed=5, reembedded=2, skipped=3),
                ) as mock_reembed,
                patch(
                    "utils.query_cache.invalidate_query_caches_non_blocking",
                    new_callable=AsyncMock,
                ) as mock_invalidate,
            ):
                result = await job.run(capture_progress)

            mock_reembed.assert_called_once()
            assert mock_reembed.call_args.args[1] == "coding"
            assert result.metadata["processed"] == 5
            assert result.metadata["reembedded"] == 2
            assert result.metadata["skipped"] == 3
            assert result.metadata["by_domain"]["coding"]["processed"] == 5
            assert result.metadata["by_domain"]["coding"]["failed_offsets"] == []
            assert result.metadata["target"] == {
                "embedding_model": SERVING_MODEL,
                "embedding_model_version": SERVING_VERSION,
            }
            assert result.metadata["truncated"] is False
            assert result.metadata["truncated_domains"] == []
            mock_invalidate.assert_called_once()
            assert mock_invalidate.call_args.kwargs["trigger"] == "processor.reembed_chunks"
            assert progress_calls[-1] == 1.0

        asyncio.run(_test())

    def test_run_iterates_all_domains_when_none_given(self):
        job = _make_job(domain=None)

        async def _test():
            async def _noop(_pct: float) -> None:
                return None

            with (
                patch("app.deps.get_chroma", return_value=MagicMock()),
                patch("app.deps.get_redis", return_value=MagicMock()),
                patch.object(job, "_reembed_domain", return_value=_outcome()) as mock_reembed,
                patch(
                    "utils.query_cache.invalidate_query_caches_non_blocking",
                    new_callable=AsyncMock,
                ),
            ):
                result = await job.run(_noop)

            assert mock_reembed.call_count == len(cfg.DOMAINS)
            assert set(result.metadata["by_domain"].keys()) == set(cfg.DOMAINS)

        asyncio.run(_test())

    def test_run_skips_cache_invalidation_when_nothing_reembedded(self):
        """Covers the restamp mode too: a restamp changes no vector, so the
        query caches stay valid."""
        job = _make_job(domain="coding", restamp_only=True)

        async def _test():
            async def _noop(_pct: float) -> None:
                return None

            with (
                patch("app.deps.get_chroma", return_value=MagicMock()),
                patch("app.deps.get_redis", return_value=MagicMock()),
                patch.object(
                    job, "_reembed_domain",
                    return_value=_outcome(processed=4, restamped=4),
                ),
                patch(
                    "utils.query_cache.invalidate_query_caches_non_blocking",
                    new_callable=AsyncMock,
                ) as mock_invalidate,
            ):
                result = await job.run(_noop)

            mock_invalidate.assert_not_called()
            assert result.metadata["restamped"] == 4
            assert result.metadata["restamp_only"] is True
            assert result.metadata["dry_run"] is False

        asyncio.run(_test())

    def test_run_job_result_carries_force_flag(self):
        job = _make_job(domain="coding", force=True)

        async def _test():
            async def _noop(_pct: float) -> None:
                return None

            with (
                patch("app.deps.get_chroma", return_value=MagicMock()),
                patch("app.deps.get_redis", return_value=MagicMock()),
                patch.object(job, "_reembed_domain", return_value=_outcome(processed=1, reembedded=1)),
                patch(
                    "utils.query_cache.invalidate_query_caches_non_blocking",
                    new_callable=AsyncMock,
                ),
            ):
                result = await job.run(_noop)

            assert result.metadata["force"] is True

        asyncio.run(_test())

    def test_run_reports_truncated_when_a_domain_had_failed_pages(self):
        """AF-037: a domain that hit unreadable pages must surface as
        truncated on the JobResult, not silently reported as a clean run."""
        job = _make_job(domain="coding")

        async def _test():
            async def _noop(_pct: float) -> None:
                return None

            with (
                patch("app.deps.get_chroma", return_value=MagicMock()),
                patch("app.deps.get_redis", return_value=MagicMock()),
                patch.object(
                    job, "_reembed_domain",
                    return_value=_outcome(processed=2, reembedded=1, skipped=1, failed_offsets=[1]),
                ),
                patch(
                    "utils.query_cache.invalidate_query_caches_non_blocking",
                    new_callable=AsyncMock,
                ),
            ):
                result = await job.run(_noop)

            assert result.metadata["truncated"] is True
            assert result.metadata["truncated_domains"] == ["coding"]
            assert result.metadata["by_domain"]["coding"]["failed_offsets"] == [1]

        asyncio.run(_test())
