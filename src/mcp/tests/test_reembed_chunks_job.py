# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for app/processor/jobs/reembed_chunks.py (RAG Quality Program Phase 4.4).

Covers:
- estimate_cost(): zero-token CPU job, matching compute_entity_embeddings'
  non-LLM CostEstimate shape.
- _reembed_domain(): stale-stamp detection (mismatched or missing version),
  force=True re-embeds everything, collection.update() called with NO
  ``embeddings=`` kwarg (lets ChromaDB recompute via the bound embedder).
  AF-037: a page-read failure logs, skips past the failed offset, and is
  reported via ``failed_offsets`` instead of silently truncating the scan.
- run(): iterates config.DOMAINS when domain=None, a single domain when
  given; semantic-cache invalidation fires only when something was
  actually re-embedded; JobResult.metadata shape, including the AF-037
  ``truncated``/``truncated_domains`` failure signal.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.processor.jobs.reembed_chunks import ReembedChunksJob


def _make_job(**kwargs):
    return ReembedChunksJob(**kwargs)


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
# _reembed_domain — staleness detection
# ---------------------------------------------------------------------------


class TestReembedDomain:
    def test_stale_and_unstamped_chunks_reembedded_matching_skipped(self):
        """Three chunks: one already at the target version (skipped), one at
        a stale version, one with no stamp at all (legacy) — the latter two
        get re-embedded."""
        import config as cfg

        job = _make_job(domain="coding")
        collection = MagicMock()
        collection.get.return_value = {
            "ids": ["c1", "c2", "c3"],
            "documents": ["doc1", "doc2", "doc3"],
            "metadatas": [
                {"embedding_model_version": cfg.embedding_version_for_domain("coding")},
                {"embedding_model_version": "old-version"},
                {},  # no stamp at all — legacy chunk
            ],
        }
        chroma = MagicMock()
        chroma.get_collection.return_value = collection

        async def _test():
            return await job._reembed_domain(chroma, "coding")

        processed, reembedded, skipped, failed_offsets = asyncio.run(_test())

        assert processed == 3
        assert reembedded == 2
        assert skipped == 1
        assert failed_offsets == []

        collection.update.assert_called_once()
        call = collection.update.call_args
        assert set(call.kwargs["ids"]) == {"c2", "c3"}
        assert "embeddings" not in call.kwargs
        for meta in call.kwargs["metadatas"]:
            assert meta["embedding_model"] == cfg.EMBEDDING_MODEL
            assert meta["embedding_model_version"] == cfg.embedding_version_for_domain("coding")

    def test_force_reembeds_even_matching_stamp(self):
        import config as cfg

        job = _make_job(domain="coding", force=True)
        collection = MagicMock()
        collection.get.return_value = {
            "ids": ["c1"],
            "documents": ["doc1"],
            "metadatas": [{"embedding_model_version": cfg.embedding_version_for_domain("coding")}],
        }
        chroma = MagicMock()
        chroma.get_collection.return_value = collection

        async def _test():
            return await job._reembed_domain(chroma, "coding")

        processed, reembedded, skipped, failed_offsets = asyncio.run(_test())

        assert processed == 1
        assert reembedded == 1
        assert skipped == 0
        assert failed_offsets == []
        collection.update.assert_called_once()

    def test_no_stale_chunks_skips_update_call(self):
        import config as cfg

        job = _make_job(domain="coding")
        collection = MagicMock()
        collection.get.return_value = {
            "ids": ["c1"],
            "documents": ["doc1"],
            "metadatas": [{"embedding_model_version": cfg.embedding_version_for_domain("coding")}],
        }
        chroma = MagicMock()
        chroma.get_collection.return_value = collection

        async def _test():
            return await job._reembed_domain(chroma, "coding")

        processed, reembedded, skipped, failed_offsets = asyncio.run(_test())

        assert processed == 1
        assert reembedded == 0
        assert skipped == 1
        assert failed_offsets == []
        collection.update.assert_not_called()

    def test_missing_collection_returns_zero_without_raising(self):
        job = _make_job(domain="ghost-domain")
        chroma = MagicMock()
        chroma.get_collection.side_effect = Exception("collection not found")

        async def _test():
            return await job._reembed_domain(chroma, "ghost-domain")

        processed, reembedded, skipped, failed_offsets = asyncio.run(_test())
        assert (processed, reembedded, skipped, failed_offsets) == (0, 0, 0, [])

    def test_pagination_across_two_batches(self):
        """batch_size=1 forces two collection.get() calls before exhaustion."""
        job = _make_job(domain="coding", batch_size=1)
        collection = MagicMock()
        collection.get.side_effect = [
            {"ids": ["c1"], "documents": ["d1"], "metadatas": [{}]},
            {"ids": ["c2"], "documents": ["d2"], "metadatas": [{}]},
            {"ids": [], "documents": [], "metadatas": []},
        ]
        chroma = MagicMock()
        chroma.get_collection.return_value = collection

        async def _test():
            return await job._reembed_domain(chroma, "coding")

        processed, reembedded, skipped, failed_offsets = asyncio.run(_test())
        assert processed == 2
        assert reembedded == 2
        assert failed_offsets == []
        assert collection.get.call_count == 3

    def test_one_bad_page_does_not_abort_the_scan(self):
        """AF-037: a transient failure on one page must not truncate the
        domain scan — the loop advances past the failed offset and keeps
        reading subsequent pages, reporting the failure instead of hiding it."""
        job = _make_job(domain="coding", batch_size=1)
        collection = MagicMock()
        collection.get.side_effect = [
            {"ids": ["c1"], "documents": ["d1"], "metadatas": [{}]},
            RuntimeError("transient chroma read error"),
            {"ids": ["c3"], "documents": ["d3"], "metadatas": [{}]},
            {"ids": [], "documents": [], "metadatas": []},
        ]
        chroma = MagicMock()
        chroma.get_collection.return_value = collection

        async def _test():
            return await job._reembed_domain(chroma, "coding")

        with patch("core.utils.swallowed.log_swallowed_error"):
            processed, reembedded, skipped, failed_offsets = asyncio.run(_test())

        # c1 and c3 were both reachable and processed despite the failure at
        # offset=1 sitting between them — the scan did not stop early.
        assert processed == 2
        assert reembedded == 2
        assert failed_offsets == [1]

    def test_every_page_failing_aborts_after_max_consecutive_failures(self):
        """A collection that fails on EVERY page must not spin forever."""
        from app.processor.jobs.reembed_chunks import _MAX_CONSECUTIVE_BATCH_FAILURES

        job = _make_job(domain="coding", batch_size=1)
        collection = MagicMock()
        collection.get.side_effect = RuntimeError("collection unreachable")
        chroma = MagicMock()
        chroma.get_collection.return_value = collection

        async def _test():
            return await job._reembed_domain(chroma, "coding")

        with patch("core.utils.swallowed.log_swallowed_error"):
            processed, reembedded, skipped, failed_offsets = asyncio.run(_test())

        assert processed == 0
        assert reembedded == 0
        assert len(failed_offsets) == _MAX_CONSECUTIVE_BATCH_FAILURES
        assert collection.get.call_count == _MAX_CONSECUTIVE_BATCH_FAILURES


# ---------------------------------------------------------------------------
# run() — orchestration across domains + cache invalidation
# ---------------------------------------------------------------------------


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
                patch.object(job, "_reembed_domain", return_value=(5, 2, 3, [])) as mock_reembed,
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
            assert result.metadata["by_domain"] == {
                "coding": {
                    "processed": 5, "reembedded": 2, "skipped": 3, "failed_offsets": [],
                }
            }
            assert result.metadata["truncated"] is False
            assert result.metadata["truncated_domains"] == []
            mock_invalidate.assert_called_once()
            assert mock_invalidate.call_args.kwargs["trigger"] == "processor.reembed_chunks"
            assert progress_calls[-1] == 1.0

        asyncio.run(_test())

    def test_run_iterates_all_domains_when_none_given(self):
        import config as cfg

        job = _make_job(domain=None)

        async def _test():
            async def _noop(_pct: float) -> None:
                return None

            with (
                patch("app.deps.get_chroma", return_value=MagicMock()),
                patch("app.deps.get_redis", return_value=MagicMock()),
                patch.object(job, "_reembed_domain", return_value=(0, 0, 0, [])) as mock_reembed,
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
        job = _make_job(domain="coding")

        async def _test():
            async def _noop(_pct: float) -> None:
                return None

            with (
                patch("app.deps.get_chroma", return_value=MagicMock()),
                patch("app.deps.get_redis", return_value=MagicMock()),
                patch.object(job, "_reembed_domain", return_value=(4, 0, 4, [])),
                patch(
                    "utils.query_cache.invalidate_query_caches_non_blocking",
                    new_callable=AsyncMock,
                ) as mock_invalidate,
            ):
                await job.run(_noop)

            mock_invalidate.assert_not_called()

        asyncio.run(_test())

    def test_run_job_result_carries_force_flag(self):
        job = _make_job(domain="coding", force=True)

        async def _test():
            async def _noop(_pct: float) -> None:
                return None

            with (
                patch("app.deps.get_chroma", return_value=MagicMock()),
                patch("app.deps.get_redis", return_value=MagicMock()),
                patch.object(job, "_reembed_domain", return_value=(1, 1, 0, [])),
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
                patch.object(job, "_reembed_domain", return_value=(2, 1, 1, [1])),
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
