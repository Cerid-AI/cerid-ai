# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Managed re-embed job (RAG Quality Program Phase 4.4).

Promotes ``scripts/reembed_collection.py``'s manual dual-collection
migration logic into a resumable processor job for the common case: an
in-place re-embed of a domain's LIVE collection under the model that is
already active (no collection rename, no operator shell session). The
script stays the documented path for a full dual-collection A/B
migration (stage a new model, validate, atomic-swap) — see
``docs/EMBEDDING_MIGRATIONS.md``. This job answers a narrower question:
"my `EMBEDDING_MODEL` / `EMBEDDING_MODEL_VERSIONS_PER_DOMAIN` already
changed — bring the stale chunks up to date."

Mechanics
---------
For each target domain, pages through the collection's chunks
(``documents`` + ``metadatas``, same offset-paginated shape as the
script's ``_existing_target_ids``). A chunk is "stale" when its
``embedding_model_version`` metadata does not match
``config.embedding_version_for_domain(domain)`` — including chunks with
no stamp at all (pre-Phase-4.4 legacy). Stale chunks are rewritten via
``collection.update(ids=, documents=, metadatas=)`` with NO
``embeddings=`` kwarg: ChromaDB recomputes the vector from ``documents``
using the collection's bound embedding function (the same
``_EmbeddingAwareClient``-injected embedder ``ingest_content`` uses), so
re-embedding and re-stamping happen in the same write.

Chunk TEXT never changes, so BM25 / SPLADE sparse indexes (which index
over text, not vectors) are untouched — no re-index call here, unlike
``_reingest_artifact``'s text-changing re-ingest path.

``force=True`` re-embeds every chunk regardless of its current stamp
(useful when a model was updated upstream without a version-string bump).
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

logger = logging.getLogger("ai-companion.processor.reembed_chunks")

# AF-037: a single bad page must not abort the whole domain scan (mirrors the
# per-batch update handler below), but a collection that fails on EVERY page
# (e.g. genuinely unreachable) must not spin forever advancing past failures
# it can never recover from. Bail after this many consecutive page-read
# failures within one domain.
_MAX_CONSECUTIVE_BATCH_FAILURES = 3


class ReembedChunksJob(BaseJob):
    """Re-embed chunks whose ``embedding_model_version`` stamp is stale."""

    job_type = "reembed_chunks"

    def __init__(
        self,
        domain: str | None = None,
        force: bool = False,
        batch_size: int | None = None,
        pace_s: float | None = None,
    ) -> None:
        self._domain = domain
        self._force = force
        self._batch = batch_size if batch_size is not None else config.REEMBED_JOB_BATCH_SIZE
        self._pace = pace_s if pace_s is not None else config.REEMBED_JOB_PACE_S

    @property
    def priority(self) -> Priority:
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        # CPU/local-embedder work, not an LLM call — no token cost. The
        # total chunk count isn't known before scanning the collection(s),
        # so confidence is "low" rather than the "high" compute_entity_
        # embeddings uses (that job's cost is bounded by a Neo4j count
        # fetched up front; this one discovers staleness while paging).
        return CostEstimate(
            estimated_tokens_in=0,
            estimated_tokens_out=0,
            model="cpu/embeddings",
            estimated_usd=Decimal("0.00"),
            confidence="low",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        await progress_cb(0.0)
        from app.deps import get_chroma, get_redis  # noqa: PLC0415

        chroma = get_chroma()
        domains = [self._domain] if self._domain else list(config.DOMAINS)

        total_processed = 0
        total_reembedded = 0
        total_skipped = 0
        by_domain: dict[str, dict[str, Any]] = {}
        truncated_domains: list[str] = []

        for i, domain in enumerate(domains):
            processed, reembedded, skipped, failed_offsets = await self._reembed_domain(
                chroma, domain
            )
            by_domain[domain] = {
                "processed": processed,
                "reembedded": reembedded,
                "skipped": skipped,
                "failed_offsets": failed_offsets,
            }
            if failed_offsets:
                truncated_domains.append(domain)
            total_processed += processed
            total_reembedded += reembedded
            total_skipped += skipped
            await progress_cb((i + 1) / len(domains))

        # Re-embedding changes vector geometry for every affected domain, so
        # any cached /agent/query result computed against the old vectors is
        # stale. Bust BOTH query-result caches through the unified contract —
        # the flat cache C1 was previously left stale here (AF-105); the C2-only
        # hook could not see the flat qcache:* entries.
        if total_reembedded:
            try:
                from utils.query_cache import invalidate_query_caches_non_blocking
                await invalidate_query_caches_non_blocking(
                    trigger="processor.reembed_chunks", redis=get_redis(),
                )
            except Exception as exc:  # noqa: BLE001 — observability boundary
                log_swallowed_error(
                    "app.processor.jobs.reembed_chunks.query_cache_invalidate", exc,
                )

        if truncated_domains:
            logger.warning(
                "reembed_chunks.truncated domains=%s — one or more pages failed to "
                "read; scan is incomplete, not just empty",
                truncated_domains,
            )
        logger.info(
            "reembed_chunks.done domains=%s processed=%d reembedded=%d skipped=%d "
            "force=%s truncated=%s",
            domains, total_processed, total_reembedded, total_skipped, self._force,
            truncated_domains,
        )
        return JobResult(
            job_id=f"reembed_chunks:{self._domain or 'all'}",
            actual_tokens_in=0,
            actual_tokens_out=0,
            metadata={
                "processed": total_processed,
                "reembedded": total_reembedded,
                "skipped": total_skipped,
                "by_domain": by_domain,
                "force": self._force,
                # AF-037: a domain scan that hit unreadable pages is NOT the
                # same as one that scanned everything and found nothing stale
                # — distinguish "complete" from "truncated" instead of
                # reporting COMPLETED either way.
                "truncated": bool(truncated_domains),
                "truncated_domains": truncated_domains,
            },
        )

    async def _reembed_domain(
        self, chroma: Any, domain: str,
    ) -> tuple[int, int, int, list[int]]:
        """Page through one domain's collection, re-embedding stale chunks.

        Returns ``(processed, reembedded, skipped, failed_offsets)`` for that
        domain. ``failed_offsets`` is non-empty when one or more pages could
        not be read — the caller must not report the domain as fully scanned
        when it isn't (AF-037).
        """
        target_version = config.embedding_version_for_domain(domain)
        coll_name = config.collection_name(domain)
        try:
            collection = await asyncio.to_thread(chroma.get_collection, name=coll_name)
        except Exception as exc:  # noqa: BLE001 — domain has no collection yet, nothing to do
            log_swallowed_error(
                "app.processor.jobs.reembed_chunks.get_collection", exc,
                context={"domain": domain},
            )
            return 0, 0, 0, []

        processed = 0
        reembedded = 0
        skipped = 0
        offset = 0
        failed_offsets: list[int] = []
        consecutive_failures = 0
        while True:
            try:
                batch = await asyncio.to_thread(
                    collection.get,
                    limit=self._batch,
                    offset=offset,
                    include=["documents", "metadatas"],
                )
            except Exception as exc:  # noqa: BLE001 — one bad batch must not abort the domain
                log_swallowed_error(
                    "app.processor.jobs.reembed_chunks.get_batch", exc,
                    context={"domain": domain, "offset": offset},
                )
                failed_offsets.append(offset)
                consecutive_failures += 1
                if consecutive_failures >= _MAX_CONSECUTIVE_BATCH_FAILURES:
                    logger.warning(
                        "reembed_chunks: domain=%s aborting after %d consecutive "
                        "batch-read failures at offset=%d",
                        domain, consecutive_failures, offset,
                    )
                    break
                # Advance past the failed page rather than re-reading the same
                # offset forever — we don't know how many ids that page held,
                # so best-effort skip by the configured page size.
                offset += self._batch
                continue

            consecutive_failures = 0
            ids = batch.get("ids") or []
            if not ids:
                break
            documents = batch.get("documents") or []
            metadatas = batch.get("metadatas") or []

            stale_ids: list[str] = []
            stale_docs: list[str] = []
            stale_metas: list[dict[str, Any]] = []
            for cid, doc, meta in zip(ids, documents, metadatas, strict=True):
                processed += 1
                current_meta = dict(meta or {})
                current_version = current_meta.get("embedding_model_version")
                if not self._force and current_version == target_version:
                    skipped += 1
                    continue
                current_meta["embedding_model"] = config.EMBEDDING_MODEL
                current_meta["embedding_model_version"] = target_version
                stale_ids.append(cid)
                stale_docs.append(doc)
                stale_metas.append(current_meta)

            if stale_ids:
                try:
                    # No `embeddings=` kwarg — ChromaDB recomputes the vector
                    # from `documents` via the collection's bound embedding
                    # function (chromadb.api.models.Collection.update
                    # docstring: "If embeddings are not provided, the
                    # embeddings will be computed based on documents").
                    await asyncio.to_thread(
                        collection.update,
                        ids=stale_ids,
                        documents=stale_docs,
                        metadatas=stale_metas,  # type: ignore[arg-type]
                    )
                    reembedded += len(stale_ids)
                except Exception as exc:  # noqa: BLE001 — one bad batch must not abort the domain
                    log_swallowed_error(
                        "app.processor.jobs.reembed_chunks.update_batch", exc,
                        context={"domain": domain, "offset": offset, "batch_size": len(stale_ids)},
                    )

            offset += len(ids)
            if self._pace > 0:
                await asyncio.sleep(self._pace)
            if len(ids) < self._batch:
                break

        return processed, reembedded, skipped, failed_offsets
