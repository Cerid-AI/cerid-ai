# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Managed re-embed job (RAG Quality Program Phase 4.4).

Promotes ``scripts/reembed_collection.py``'s manual dual-collection
migration logic into a resumable processor job for the common case: an
in-place re-embed of a domain's LIVE collection under the embedder that is
already serving (no collection rename, no operator shell session). The
script stays the documented path for a full dual-collection A/B
migration (stage a new model, validate, atomic-swap) — see
``docs/EMBEDDING_MIGRATIONS.md``. This job answers a narrower question:
"the serving embedder changed — bring the stale chunks up to date."

Mechanics
---------
For each target domain, pages through the collection's chunks
(``documents`` + ``metadatas``, same offset-paginated shape as the
script's ``_existing_target_ids``). A chunk is "stale" when its
``embedding_model_version`` metadata is not the serving artifact
(``core.utils.embeddings.serving_embedding_version``) — including chunks
with no stamp at all (pre-Phase-4.4 legacy). Stale chunks are rewritten via
``collection.update(ids=, documents=, metadatas=)`` with NO
``embeddings=`` kwarg: ChromaDB recomputes the vector from ``documents``
using the collection's bound embedding function (the same
``_EmbeddingAwareClient``-injected embedder ``ingest_content`` uses), so
re-embedding and re-stamping happen in the same write. Both stamp fields
come from the serving functions; ``config.EMBEDDING_MODEL`` is the ONNX
pin, not necessarily the producer, and stamping it here is how ~700
nomic-produced chunks came to say Snowflake.

``restamp_only=True`` fixes exactly that mislabel without re-embedding: for
each stale chunk it re-embeds the text with the serving embedder and, when
the stored vector is already in the serving space (the boot probe's check,
``app.startup.invariants.in_serving_space``), rewrites only the two stamp
fields. A vector the serving embedder cannot reproduce keeps its old stamp
and is counted ``out_of_serving_space`` — a restamp must never claim a
vector it did not produce; re-embed those. ``dry_run=True`` does the same
walk and writes nothing, reporting counts per current stamp value.

Chunk TEXT never changes, so BM25 / SPLADE sparse indexes (which index
over text, not vectors) are untouched — no re-index call here, unlike
``_reingest_artifact``'s text-changing re-ingest path.

``force=True`` re-embeds every chunk regardless of its current stamp
(useful when a model was updated upstream without a version-string bump).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

import config
from app.startup.invariants import in_serving_space
from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobResult, ProgressCallback
from core.processor.priority import Priority
from core.utils.embeddings import (
    get_embedding_function,
    serving_embedding_model,
    serving_embedding_version,
)
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.reembed_chunks")

# AF-037: a single bad page must not abort the whole domain scan (mirrors the
# per-batch update handler below), but a collection that fails on EVERY page
# (e.g. genuinely unreachable) must not spin forever advancing past failures
# it can never recover from. Bail after this many consecutive page-read
# failures within one domain.
_MAX_CONSECUTIVE_BATCH_FAILURES = 3

_UNSTAMPED = "unstamped"


@dataclass
class DomainOutcome:
    """What one domain's scan did. ``failed_offsets`` is non-empty when one or
    more pages could not be read — the caller must not report the domain as
    fully scanned when it isn't (AF-037). ``by_stamp`` (restamp mode) counts
    stale chunks per current ``embedding_model / embedding_model_version``
    value, split by whether the stored vector is in the serving space."""

    processed: int = 0
    reembedded: int = 0
    restamped: int = 0
    skipped: int = 0
    in_serving_space: int = 0
    out_of_serving_space: int = 0
    failed_offsets: list[int] = field(default_factory=list)
    by_stamp: dict[str, dict[str, int]] = field(default_factory=dict)


def _stamp_key(meta: dict[str, Any]) -> str:
    model = meta.get("embedding_model") or _UNSTAMPED
    version = meta.get("embedding_model_version") or _UNSTAMPED
    return f"{model} / {version}"


class ReembedChunksJob(BaseJob):
    """Re-embed (or restamp) chunks whose stamp is not the serving artifact."""

    job_type = "reembed_chunks"

    def __init__(
        self,
        domain: str | None = None,
        force: bool = False,
        batch_size: int | None = None,
        pace_s: float | None = None,
        restamp_only: bool = False,
        dry_run: bool = False,
    ) -> None:
        self._domain = domain
        self._force = force
        self._restamp_only = restamp_only
        self._dry_run = dry_run
        self._batch = batch_size if batch_size is not None else config.REEMBED_JOB_BATCH_SIZE
        self._pace = pace_s if pace_s is not None else config.REEMBED_JOB_PACE_S
        self._target: dict[str, str] | None = None

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

    def _target_stamp(self) -> dict[str, str]:
        """The stamp every touched chunk gets — resolved once per run so a
        provider flip mid-scan cannot split one run across two artifacts."""
        if self._target is None:
            self._target = {
                "embedding_model": serving_embedding_model(),
                "embedding_model_version": serving_embedding_version(),
            }
        return self._target

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        await progress_cb(0.0)
        from app.deps import get_chroma, get_redis  # noqa: PLC0415

        chroma = get_chroma()
        domains = [self._domain] if self._domain else list(config.DOMAINS)
        target = self._target_stamp()

        totals = DomainOutcome()
        by_domain: dict[str, dict[str, Any]] = {}
        truncated_domains: list[str] = []

        for i, domain in enumerate(domains):
            out = await self._reembed_domain(chroma, domain)
            by_domain[domain] = asdict(out)
            if out.failed_offsets:
                truncated_domains.append(domain)
            totals.processed += out.processed
            totals.reembedded += out.reembedded
            totals.restamped += out.restamped
            totals.skipped += out.skipped
            totals.in_serving_space += out.in_serving_space
            totals.out_of_serving_space += out.out_of_serving_space
            await progress_cb((i + 1) / len(domains))

        # Re-embedding changes vector geometry for every affected domain, so
        # any cached /agent/query result computed against the old vectors is
        # stale. Bust BOTH query-result caches through the unified contract —
        # the flat cache C1 was previously left stale here (AF-105); the C2-only
        # hook could not see the flat qcache:* entries. A restamp changes no
        # vector, so it leaves the caches alone.
        if totals.reembedded:
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
            "reembed_chunks.done domains=%s processed=%d reembedded=%d restamped=%d "
            "skipped=%d out_of_serving_space=%d force=%s restamp_only=%s dry_run=%s "
            "target=%s truncated=%s",
            domains, totals.processed, totals.reembedded, totals.restamped,
            totals.skipped, totals.out_of_serving_space, self._force,
            self._restamp_only, self._dry_run, target, truncated_domains,
        )
        return JobResult(
            job_id=f"reembed_chunks:{self._domain or 'all'}",
            actual_tokens_in=0,
            actual_tokens_out=0,
            metadata={
                "processed": totals.processed,
                "reembedded": totals.reembedded,
                "restamped": totals.restamped,
                "skipped": totals.skipped,
                "in_serving_space": totals.in_serving_space,
                "out_of_serving_space": totals.out_of_serving_space,
                "by_domain": by_domain,
                "target": target,
                "force": self._force,
                "restamp_only": self._restamp_only,
                "dry_run": self._dry_run,
                # AF-037: a domain scan that hit unreadable pages is NOT the
                # same as one that scanned everything and found nothing stale
                # — distinguish "complete" from "truncated" instead of
                # reporting COMPLETED either way.
                "truncated": bool(truncated_domains),
                "truncated_domains": truncated_domains,
            },
        )

    async def _reembed_domain(self, chroma: Any, domain: str) -> DomainOutcome:
        """Page through one domain's collection, re-embedding (or restamping)
        the chunks whose stamp is not the serving artifact."""
        target = self._target_stamp()
        target_version = target["embedding_model_version"]
        coll_name = config.collection_name(domain)
        out = DomainOutcome()
        try:
            collection = await asyncio.to_thread(chroma.get_collection, name=coll_name)
        except Exception as exc:  # noqa: BLE001 — domain has no collection yet, nothing to do
            log_swallowed_error(
                "app.processor.jobs.reembed_chunks.get_collection", exc,
                context={"domain": domain},
            )
            return out

        include = ["documents", "metadatas"]
        if self._restamp_only:
            include.append("embeddings")

        offset = 0
        consecutive_failures = 0
        while True:
            try:
                batch = await asyncio.to_thread(
                    collection.get, limit=self._batch, offset=offset, include=include,
                )
            except Exception as exc:  # noqa: BLE001 — one bad batch must not abort the domain
                log_swallowed_error(
                    "app.processor.jobs.reembed_chunks.get_batch", exc,
                    context={"domain": domain, "offset": offset},
                )
                out.failed_offsets.append(offset)
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
            raw_embeddings = batch.get("embeddings")
            embeddings = list(raw_embeddings) if raw_embeddings is not None else [None] * len(ids)

            stale: list[tuple[str, str, dict[str, Any], Any]] = []
            for cid, doc, meta, emb in zip(ids, documents, metadatas, embeddings, strict=True):
                out.processed += 1
                current_meta = dict(meta or {})
                if not self._force and current_meta.get("embedding_model_version") == target_version:
                    out.skipped += 1
                    continue
                stale.append((cid, doc, current_meta, emb))

            if stale:
                if self._restamp_only:
                    await self._restamp_batch(collection, domain, offset, stale, out)
                else:
                    await self._reembed_batch(collection, domain, offset, stale, out)

            offset += len(ids)
            if self._pace > 0:
                await asyncio.sleep(self._pace)
            if len(ids) < self._batch:
                break

        return out

    async def _reembed_batch(
        self,
        collection: Any,
        domain: str,
        offset: int,
        stale: list[tuple[str, str, dict[str, Any], Any]],
        out: DomainOutcome,
    ) -> None:
        target = self._target_stamp()
        ids = [cid for cid, _, _, _ in stale]
        docs = [doc for _, doc, _, _ in stale]
        metas = [{**meta, **target} for _, _, meta, _ in stale]
        try:
            # No `embeddings=` kwarg — ChromaDB recomputes the vector from
            # `documents` via the collection's bound embedding function
            # (chromadb.api.models.Collection.update docstring: "If embeddings
            # are not provided, the embeddings will be computed based on
            # documents").
            await asyncio.to_thread(
                collection.update, ids=ids, documents=docs, metadatas=metas,
            )
            out.reembedded += len(ids)
        except Exception as exc:  # noqa: BLE001 — one bad batch must not abort the domain
            log_swallowed_error(
                "app.processor.jobs.reembed_chunks.update_batch", exc,
                context={"domain": domain, "offset": offset, "batch_size": len(ids)},
            )

    async def _restamp_batch(
        self,
        collection: Any,
        domain: str,
        offset: int,
        stale: list[tuple[str, str, dict[str, Any], Any]],
        out: DomainOutcome,
    ) -> None:
        target = self._target_stamp()
        embed = get_embedding_function()
        fresh: list[Any]
        if embed is None:
            # Store-side embedding: nothing in this process can reproduce a
            # stored vector, so nothing can be shown to be in the serving space.
            fresh = [None] * len(stale)
        else:
            try:
                fresh = list(await asyncio.to_thread(embed, [doc for _, doc, _, _ in stale]))
            except Exception as exc:  # noqa: BLE001 — one bad batch must not abort the domain
                log_swallowed_error(
                    "app.processor.jobs.reembed_chunks.restamp_embed", exc,
                    context={"domain": domain, "offset": offset, "batch_size": len(stale)},
                )
                out.failed_offsets.append(offset)
                return

        ids: list[str] = []
        metas: list[dict[str, Any]] = []
        for (cid, _, meta, stored), vec in zip(stale, fresh, strict=True):
            bucket = out.by_stamp.setdefault(
                _stamp_key(meta), {"in_serving_space": 0, "out_of_serving_space": 0},
            )
            if stored is not None and vec is not None and in_serving_space(stored, vec):
                bucket["in_serving_space"] += 1
                out.in_serving_space += 1
                ids.append(cid)
                metas.append({**meta, **target})
            else:
                bucket["out_of_serving_space"] += 1
                out.out_of_serving_space += 1

        if self._dry_run or not ids:
            return
        try:
            # metadatas only — the vector stays exactly as stored.
            await asyncio.to_thread(collection.update, ids=ids, metadatas=metas)
            out.restamped += len(ids)
        except Exception as exc:  # noqa: BLE001 — one bad batch must not abort the domain
            log_swallowed_error(
                "app.processor.jobs.reembed_chunks.restamp_batch", exc,
                context={"domain": domain, "offset": offset, "batch_size": len(ids)},
            )
