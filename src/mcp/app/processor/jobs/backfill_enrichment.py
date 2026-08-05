# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Track A enrichment backfill (RAG Quality Program Phase 5.3).

Enriches `sub_category` + `tags` on artifacts that the pre-5.1 ingest paths
(memory / connector / digest / text_input) left bare — the same enrichment
Slice 5.1 now applies at ingest time, applied retroactively to the backlog.

Track A is **metadata-only**: it writes `sub_category` + `tags` to Neo4j
(via :func:`update_artifact_taxonomy` — the same write `recategorize` uses)
and merges them into the artifact's existing Chroma chunk metadata. It
**never changes domain**, so no chunks move between per-domain collections
and the HARD CONSTRAINT (conversations are never re-domained) holds by
construction — conversation artifacts are enriched in place like any other.

Domain *corrections* are Track B: they require moving chunks between
collections, so they go through `recategorize()` in small operator-driven
batches off the `needs-review` queue (:func:`find_needs_review_artifacts`),
NOT through this blanket backfill.

Idempotent: the scan selects only bare-tag artifacts, so a re-run skips
everything already enriched. Paced + batch-capped (the classifier runs per
artifact). Nightly until the backlog drains, then the scan returns 0 and the
job self-idles.
"""
from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal
from typing import Any

import config
from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobResult, ProgressCallback
from core.processor.priority import Priority
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.backfill_enrichment")


def _fetch_bare_artifacts(driver: Any, limit: int) -> list[dict[str, Any]]:
    """One indexed read: artifacts with no tags but with chunk IDs.

    `a.tags` is the Neo4j-side tag list; bare means null / empty-json / empty.
    Artifacts without chunk_ids can't be re-classified (no content) — skipped.
    """
    cypher = """
        MATCH (a:Artifact)
        WHERE (a.tags IS NULL OR a.tags = '' OR a.tags = '[]')
          AND a.chunk_ids IS NOT NULL AND a.chunk_ids <> '[]'
        RETURN a.id AS id, a.domain AS domain, a.filename AS filename,
               a.chunk_ids AS chunk_ids
        LIMIT $limit
    """
    with driver.session() as session:
        return [dict(r) for r in session.run(cypher, limit=limit)]


def find_needs_review_artifacts(driver: Any, limit: int = 200) -> list[dict[str, Any]]:
    """Track B queue — artifacts the classifier flagged `needs-review`
    (Phase 5.2 low-confidence demotion). Operators drive `recategorize()`
    from this list to apply domain corrections in paced batches; this is a
    read-only surface, never a blanket re-classifier.
    """
    cypher = """
        MATCH (a:Artifact)-[:TAGGED_WITH]->(t:Tag {name: 'needs-review'})
        RETURN a.id AS id, a.domain AS domain, a.filename AS filename,
               a.sub_category AS sub_category
        LIMIT $limit
    """
    with driver.session() as session:
        return [dict(r) for r in session.run(cypher, limit=limit)]


class BackfillEnrichmentJob(BaseJob):
    """Track A: enrich tags + sub_category on bare artifacts in place."""

    job_type = "backfill_enrichment"

    def __init__(
        self,
        tenant_id: str = "default",
        batch_size: int | None = None,
        pace_s: float | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._batch = batch_size if batch_size is not None else config.BACKFILL_ENRICHMENT_BATCH
        self._pace = pace_s if pace_s is not None else config.BACKFILL_ENRICHMENT_PACE_S

    @property
    def priority(self) -> Priority:
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        # One small classifier call per artifact; bounded by batch size.
        return CostEstimate(
            estimated_tokens_in=self._batch * 400,
            estimated_tokens_out=self._batch * 60,
            model=config.CATEGORIZE_MODELS.get("smart", "internal"),
            estimated_usd=Decimal("0.00"),
            confidence="medium",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        await progress_cb(0.0)
        from app.deps import get_chroma, get_neo4j  # noqa: PLC0415

        driver = get_neo4j()
        if driver is None:
            return JobResult(
                job_id=f"backfill_enrichment:{self._tenant_id}",
                actual_tokens_in=0, actual_tokens_out=0,
                metadata={"status": "skipped", "reason": "neo4j unavailable"},
            )
        chroma = get_chroma()

        rows = await asyncio.to_thread(_fetch_bare_artifacts, driver, self._batch)
        await progress_cb(0.1)
        if not rows:
            logger.info("backfill_enrichment: backlog drained (0 bare artifacts)")
            return JobResult(
                job_id=f"backfill_enrichment:{self._tenant_id}",
                actual_tokens_in=0, actual_tokens_out=0,
                metadata={"enriched": 0, "scanned": 0, "drained": True},
            )

        enriched = 0
        for i, row in enumerate(rows):
            try:
                if await self._enrich_one(driver, chroma, row):
                    enriched += 1
            except Exception as exc:  # noqa: BLE001 — one bad artifact must not abort the batch
                log_swallowed_error("backfill_enrichment.enrich_one", exc)
            await progress_cb(0.1 + 0.9 * (i + 1) / len(rows))
            if self._pace > 0:
                await asyncio.sleep(self._pace)

        logger.info("backfill_enrichment.done scanned=%d enriched=%d", len(rows), enriched)
        return JobResult(
            job_id=f"backfill_enrichment:{self._tenant_id}",
            actual_tokens_in=0, actual_tokens_out=0,
            metadata={"enriched": enriched, "scanned": len(rows), "drained": False},
        )

    async def _enrich_one(self, driver: Any, chroma: Any, row: dict[str, Any]) -> bool:
        """Classify one artifact and write tags + sub_category. Returns True
        when something was written. NEVER changes domain (Track A)."""
        artifact_id = row["id"]
        domain = row.get("domain") or "general"
        chunk_ids = json.loads(row.get("chunk_ids") or "[]")
        if not chunk_ids:
            return False

        collection = chroma.get_or_create_collection(name=config.collection_name(domain))
        fetched = await asyncio.to_thread(
            lambda: collection.get(ids=chunk_ids, include=["documents", "metadatas"])
        )
        docs = fetched.get("documents") or []
        content = "\n".join(d for d in docs if d)[: config.AI_SNIPPET_MAX_CHARS * 2]
        if not content.strip():
            return False

        from utils.metadata import ai_categorize  # noqa: PLC0415

        enr = await ai_categorize(content, row.get("filename") or "backfill") or {}
        sub_category = enr.get("sub_category") or ""
        tags = [t for t in (enr.get("tags") or []) if isinstance(t, str) and t.strip()]
        if not sub_category and not tags:
            return False

        tags_json = json.dumps(tags) if tags else None

        # Neo4j — sub_category + tags + edges, no domain change.
        from app.db.neo4j.taxonomy import update_artifact_taxonomy  # noqa: PLC0415
        await asyncio.to_thread(
            update_artifact_taxonomy, driver, artifact_id,
            sub_category or config.DEFAULT_SUB_CATEGORY, tags_json,
        )

        # Chroma — merge the two fields into each chunk's existing metadata
        # (no collection move; domain stays put).
        ids = fetched.get("ids") or []
        metas = fetched.get("metadatas") or []
        if ids and metas:
            new_metas = []
            for meta in metas:
                m = dict(meta or {})
                if sub_category:
                    m["sub_category"] = sub_category
                if tags_json:
                    m["tags_json"] = tags_json
                new_metas.append(m)
            await asyncio.to_thread(lambda: collection.update(ids=ids, metadatas=new_metas))
        return True
