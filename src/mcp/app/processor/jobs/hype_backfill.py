# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""HyPE backfill job — index EXISTING chunks (AF-049).

HyPE (Hypothetical Prompt Embeddings) indexing runs only at ingest time
(``app.services.ingestion._enqueue_hype_jobs_if_enabled``), so flipping
``RETRIEVAL_HYPE_ENABLED`` on only ever covers chunks ingested *after* the
flip — every chunk already in the KB stays HyPE-less, and retrieval never
benefits from HyPE for the existing corpus. This job closes that gap: it pages
each domain's base collection and, for every chunk that has no companion HyPE
entry yet, generates + stores its hypothetical-question embeddings via the same
``index_chunk_with_hype`` seam the ingest path uses.

Mechanics (mirrors ``ReembedChunksJob``'s page-the-collection shape)
--------------------------------------------------------------------
For each target domain:

1. Build the set of already-indexed ``source_chunk_id`` values by paging the
   companion ``cerid_{domain}_hype`` collection's metadata (empty when the
   collection doesn't exist yet — the first backfill).
2. Page the base ``cerid_{domain}`` collection. For each chunk NOT already
   indexed (and with non-empty text), call ``index_chunk_with_hype`` — an LLM
   call per chunk, so the run is bounded by ``max_chunks`` and paced by
   ``pace_s``.

Idempotent + resumable: ``index_chunk_with_hype`` upserts, and the skip-set
means a re-run picks up only the chunks a capped previous run didn't reach, so
repeated runs converge without re-spending on done chunks. ``force=True``
re-indexes every chunk regardless of the skip-set.

Cost: unlike ``ReembedChunksJob`` (CPU re-embed), each indexed chunk is an LLM
call (~3 000 in / 800 out on the local Ollama path, $0). The per-run
``max_chunks`` cap keeps a single run's spend bounded; a truncated run logs the
cap so the operator knows to re-run.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any

import config
from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobResult, ProgressCallback
from core.processor.priority import Priority
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.hype_backfill")

# Per-chunk local-Ollama token estimate (matches HyPEIndexingJob).
_EST_TOKENS_IN_PER_CHUNK = 3_000
_EST_TOKENS_OUT_PER_CHUNK = 800


class HypeBackfillJob(BaseJob):
    """Backfill HyPE question embeddings for chunks ingested before the flag."""

    job_type = "hype_backfill"

    def __init__(
        self,
        domain: str | None = None,
        force: bool = False,
        batch_size: int | None = None,
        max_chunks: int | None = None,
        pace_s: float | None = None,
        n: int = 5,
    ) -> None:
        self._domain = domain
        self._force = force
        self._batch = (
            batch_size if batch_size is not None else config.HYPE_BACKFILL_BATCH_SIZE
        )
        self._max_chunks = (
            max_chunks if max_chunks is not None else config.HYPE_BACKFILL_MAX_CHUNKS
        )
        self._pace = pace_s if pace_s is not None else config.HYPE_BACKFILL_PACE_S
        self._n = n

    @property
    def priority(self) -> Priority:
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        # Bounded by max_chunks LLM calls on the local Ollama path ($0). The
        # true count is only known while scanning, so confidence is "low".
        return CostEstimate(
            estimated_tokens_in=_EST_TOKENS_IN_PER_CHUNK * self._max_chunks,
            estimated_tokens_out=_EST_TOKENS_OUT_PER_CHUNK * self._max_chunks,
            model="ollama/local",
            estimated_usd=Decimal("0.00"),
            confidence="low",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        await progress_cb(0.0)

        # Backfilling while the flag is off would enqueue LLM work whose output
        # retrieval never reads — index_chunk_with_hype itself no-ops when off.
        from app.services.hype_indexer import _hype_enabled  # noqa: PLC0415
        if not _hype_enabled():
            logger.info("hype_backfill.skip flag RETRIEVAL_HYPE_ENABLED is off")
            await progress_cb(1.0)
            return JobResult(
                job_id=f"hype_backfill:{self._domain or 'all'}",
                actual_tokens_in=0,
                actual_tokens_out=0,
                metadata={"enabled": False, "indexed": 0},
            )

        from app.deps import get_chroma, get_redis  # noqa: PLC0415

        chroma = get_chroma()
        domains = [self._domain] if self._domain else list(config.DOMAINS)

        totals = {"scanned": 0, "indexed": 0, "skipped": 0, "empty": 0, "errors": 0}
        by_domain: dict[str, dict[str, int]] = {}
        budget = self._max_chunks
        capped = False

        for i, domain in enumerate(domains):
            counts, budget = await self._backfill_domain(chroma, domain, budget)
            by_domain[domain] = counts
            for k in totals:
                totals[k] += counts[k]
            await progress_cb((i + 1) / len(domains))
            if budget <= 0:
                capped = True
                break

        # New HyPE hits change /agent/query results (retrieval-time dedup merges
        # them), so any cached result computed before the backfill is stale.
        # Bust both query caches through the unified contract, exactly as
        # ReembedChunksJob does after changing vector geometry.
        if totals["indexed"]:
            try:
                from utils.query_cache import invalidate_query_caches_non_blocking
                await invalidate_query_caches_non_blocking(
                    trigger="processor.hype_backfill", redis=get_redis(),
                )
            except Exception as exc:  # noqa: BLE001 — observability boundary
                log_swallowed_error(
                    "app.processor.jobs.hype_backfill.query_cache_invalidate", exc,
                )

        if capped:
            logger.info(
                "hype_backfill.capped max_chunks=%d reached — re-run to continue "
                "(already-indexed chunks are skipped)", self._max_chunks,
            )
        logger.info(
            "hype_backfill.done domains=%s scanned=%d indexed=%d skipped=%d "
            "empty=%d errors=%d capped=%s force=%s",
            domains, totals["scanned"], totals["indexed"], totals["skipped"],
            totals["empty"], totals["errors"], capped, self._force,
        )
        return JobResult(
            job_id=f"hype_backfill:{self._domain or 'all'}",
            actual_tokens_in=_EST_TOKENS_IN_PER_CHUNK * totals["indexed"],
            actual_tokens_out=_EST_TOKENS_OUT_PER_CHUNK * totals["indexed"],
            metadata={
                "enabled": True,
                **totals,
                "capped": capped,
                "by_domain": by_domain,
                "force": self._force,
            },
        )

    async def _already_indexed(self, chroma: Any, coll_name: str) -> set[str]:
        """Set of ``source_chunk_id`` already present in the HyPE companion
        collection (empty when it doesn't exist yet)."""
        from core.retrieval.hype_index import hype_collection_name  # noqa: PLC0415

        hype_name = hype_collection_name(coll_name)
        try:
            hype_coll = await asyncio.to_thread(chroma.get_collection, name=hype_name)
        except Exception as exc:  # noqa: BLE001 — no HyPE collection yet: nothing indexed
            log_swallowed_error(
                "app.processor.jobs.hype_backfill.get_hype_collection", exc,
                context={"collection": hype_name},
            )
            return set()

        seen: set[str] = set()
        offset = 0
        while True:
            try:
                batch = await asyncio.to_thread(
                    hype_coll.get, limit=self._batch, offset=offset,
                    include=["metadatas"],
                )
            except Exception as exc:  # noqa: BLE001 — observability boundary
                log_swallowed_error(
                    "app.processor.jobs.hype_backfill.page_hype", exc,
                    context={"collection": hype_name, "offset": offset},
                )
                break
            ids = batch.get("ids") or []
            if not ids:
                break
            for meta in batch.get("metadatas") or []:
                src = (meta or {}).get("source_chunk_id")
                if src:
                    seen.add(src)
            offset += len(ids)
            if len(ids) < self._batch:
                break
        return seen

    async def _backfill_domain(
        self, chroma: Any, domain: str, budget: int,
    ) -> tuple[dict[str, int], int]:
        """Page one domain's base collection, indexing un-indexed chunks up to
        ``budget``. Returns ``(counts, remaining_budget)``."""
        counts = {"scanned": 0, "indexed": 0, "skipped": 0, "empty": 0, "errors": 0}
        coll_name = config.collection_name(domain)
        try:
            collection = await asyncio.to_thread(chroma.get_collection, name=coll_name)
        except Exception as exc:  # noqa: BLE001 — domain has no collection yet
            log_swallowed_error(
                "app.processor.jobs.hype_backfill.get_collection", exc,
                context={"domain": domain},
            )
            return counts, budget

        already = set() if self._force else await self._already_indexed(chroma, coll_name)

        from app.services.hype_indexer import index_chunk_with_hype  # noqa: PLC0415

        offset = 0
        while budget > 0:
            try:
                batch = await asyncio.to_thread(
                    collection.get, limit=self._batch, offset=offset,
                    include=["documents", "metadatas"],
                )
            except Exception as exc:  # noqa: BLE001 — observability boundary
                log_swallowed_error(
                    "app.processor.jobs.hype_backfill.get_batch", exc,
                    context={"domain": domain, "offset": offset},
                )
                break
            ids = batch.get("ids") or []
            if not ids:
                break
            documents = batch.get("documents") or []
            metadatas = batch.get("metadatas") or []

            for cid, doc, meta in zip(ids, documents, metadatas, strict=True):
                counts["scanned"] += 1
                if budget <= 0:
                    break
                if cid in already:
                    counts["skipped"] += 1
                    continue
                if not (doc or "").strip():
                    counts["empty"] += 1
                    continue
                artifact_id = (meta or {}).get("artifact_id", "")
                try:
                    result = await index_chunk_with_hype(
                        cid, doc, collection_name=coll_name,
                        artifact_id=artifact_id, chroma=chroma, n=self._n,
                    )
                except Exception as exc:  # noqa: BLE001 — one bad chunk must not abort the domain
                    log_swallowed_error(
                        "app.processor.jobs.hype_backfill.index_chunk", exc,
                        context={"domain": domain, "chunk_id": cid},
                    )
                    counts["errors"] += 1
                    continue
                if not result.get("enabled", False):
                    # Flag flipped off mid-run — stop cleanly.
                    return counts, 0
                counts["indexed"] += 1
                budget -= 1
                if self._pace > 0:
                    await asyncio.sleep(self._pace)

            offset += len(ids)
            if len(ids) < self._batch:
                break

        return counts, budget
