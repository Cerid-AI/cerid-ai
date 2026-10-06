# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""QuickCaptureEnrichJob — enrich a quick-captured note after it is persisted.

``POST /upload?quick=true`` stores the note with a provisional domain and
minimal metadata and acknowledges at once; this job does what the inline
path used to do while the user waited (45 s on the Studio when a wiki
refresh held the chat slot): classify, correct the domain, write
sub-category + tags, derive a display title, and tell the FAB.

Domain corrections go through ``app.routers.artifacts.recategorize`` — the
one helper that moves chunks between per-domain collections and keeps the
lexical indexes and caches in step. Entity extraction is not repeated here:
``ingest_content`` already enqueued it at persist time.

Idempotent: the first successful run stamps ``quick_capture_enriched_at``
on the Artifact node and a re-run returns without a classifier call.
Discovered automatically by ``build_default_registry()``.
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
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.processor.quick_capture_enrich")

_AGENT = "quick_capture"


def enqueue_quick_capture_enrichment(
    artifact_id: str,
    *,
    domain_locked: bool = False,
    categorize_mode: str = "",
) -> str | None:
    """Queue one enrichment per artifact; a pending or running job for the
    same artifact absorbs the request. Returns the job id, or ``None`` when
    collapsed onto an existing job."""
    from app.db.redis.processor_queue import enqueue_job_if_absent  # noqa: PLC0415

    payload: dict[str, Any] = {
        "artifact_id": artifact_id,
        "tenant_id": "default",
        "domain_locked": domain_locked,
        "categorize_mode": categorize_mode,
    }
    job = QuickCaptureEnrichJob(**payload)
    return enqueue_job_if_absent(
        job, payload=payload, dedupe_payload={"artifact_id": artifact_id},
    )


class QuickCaptureEnrichJob(BaseJob):
    """Classify, re-domain, tag and title one quick-captured artifact."""

    job_type = "quick_capture_enrich"

    def __init__(
        self,
        artifact_id: str,
        tenant_id: str = "default",
        domain_locked: bool = False,
        categorize_mode: str = "",
    ) -> None:
        self._artifact_id = artifact_id
        self._tenant_id = tenant_id
        self._domain_locked = domain_locked
        self._mode = categorize_mode or config.CATEGORIZE_MODE

    @property
    def priority(self) -> Priority:
        # The user just saved the note and is watching for its title.
        return Priority.HIGH

    def estimate_cost(self) -> CostEstimate:
        return CostEstimate(
            estimated_tokens_in=400,
            estimated_tokens_out=60,
            model=config.CATEGORIZE_MODELS.get(self._mode, "internal"),
            estimated_usd=Decimal("0.00"),
            confidence="medium",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        await progress_cb(0.0)
        try:
            meta = await self._enrich(progress_cb)
        except Exception as exc:
            log_swallowed_error(
                "processor.quick_capture_enrich", exc,
                context={"artifact_id": self._artifact_id},
            )
            raise
        await progress_cb(1.0)
        return JobResult(
            job_id="",  # filled in by the worker after dequeue
            actual_tokens_in=0,
            actual_tokens_out=0,
            metadata={"artifact_id": self._artifact_id, **meta},
        )

    async def _enrich(self, progress_cb: ProgressCallback) -> dict[str, Any]:
        from app.db.neo4j.artifacts import set_artifact_properties  # noqa: PLC0415
        from app.deps import get_chroma, get_neo4j  # noqa: PLC0415

        driver = get_neo4j()
        chroma = get_chroma()

        state = await asyncio.to_thread(_fetch_state, driver, self._artifact_id)
        if state is None:
            logger.info("quick_capture_enrich.skipped artifact=%s reason=artifact_not_found", self._artifact_id)
            return {"skipped": "artifact_not_found"}
        if state.get("enriched_at"):
            logger.info("quick_capture_enrich.skipped artifact=%s reason=already_enriched", self._artifact_id)
            return {"skipped": "already_enriched", "domain": state["domain"]}

        domain = state["domain"] or config.DEFAULT_DOMAIN
        filename = state["filename"] or ""
        chunk_ids = _parse_ids(state.get("chunk_ids"))
        collection = chroma.get_or_create_collection(name=config.collection_name(domain))
        fetched = await asyncio.to_thread(
            lambda: collection.get(ids=chunk_ids, include=["documents", "metadatas"])
        ) if chunk_ids else {"ids": [], "documents": [], "metadatas": []}
        content = "\n".join(d for d in (fetched.get("documents") or []) if d)
        if not content.strip():
            logger.info("quick_capture_enrich.skipped artifact=%s reason=no_content", self._artifact_id)
            return {"skipped": "no_content", "domain": domain}
        await progress_cb(0.2)

        # Title first: deterministic, no model, and worth having even when the
        # classifier below is unavailable.
        from core.utils.title_derivation import derive_title  # noqa: PLC0415

        title = derive_title(content) or filename
        props: dict[str, Any] = {}
        chunk_patch: dict[str, Any] = {"metadata_mode": "enriched"}
        if title and title != filename:
            props["filename"] = title
            props["title_derived"] = "true"
            chunk_patch["filename"] = title
            chunk_patch["title_derived"] = "true"

        enrichment: dict[str, Any] = {}
        if self._mode != "manual":
            from utils.metadata import ai_categorize  # noqa: PLC0415

            enrichment = await ai_categorize(content, filename, self._mode) or {}
            if not enrichment:
                # Best-effort, like the inline path was: keep the note where it
                # is, leave the stamp off so a later run can still enrich.
                if props:
                    await asyncio.to_thread(set_artifact_properties, driver, self._artifact_id, props)
                    await _patch_chunks(collection, fetched, {k: v for k, v in chunk_patch.items() if k != "metadata_mode"})
                self._emit(title, domain, "", enriched=False)
                logger.warning("quick_capture_enrich.skipped artifact=%s reason=classifier_unavailable", self._artifact_id)
                return {"skipped": "classifier_unavailable", "domain": domain, "title": title}
        await progress_cb(0.6)

        sub_category = str(enrichment.get("sub_category") or "")
        tags = [t for t in (enrichment.get("tags") or []) if isinstance(t, str) and t.strip()]
        tags_json = json.dumps(tags) if tags else ""
        suggested = str(enrichment.get("suggested_domain") or "")
        moved = False
        if (
            not self._domain_locked
            and suggested
            and suggested != domain
            and suggested in config.DOMAINS
            and chunk_ids
        ):
            from app.routers.artifacts import recategorize  # noqa: PLC0415

            await asyncio.to_thread(recategorize, self._artifact_id, suggested, sub_category, tags_json)
            domain = suggested
            moved = True
            collection = chroma.get_or_create_collection(name=config.collection_name(domain))
            fetched = await asyncio.to_thread(
                lambda: collection.get(ids=chunk_ids, include=["documents", "metadatas"])
            )
        elif sub_category or tags_json:
            from app.db.neo4j.taxonomy import update_artifact_taxonomy  # noqa: PLC0415

            await asyncio.to_thread(
                update_artifact_taxonomy, driver, self._artifact_id,
                sub_category or config.DEFAULT_SUB_CATEGORY, tags_json or None,
            )
        if sub_category:
            chunk_patch["sub_category"] = sub_category
        if tags_json:
            chunk_patch["tags_json"] = tags_json
        await progress_cb(0.8)

        props["quick_capture_enriched_at"] = utcnow_iso()
        await asyncio.to_thread(set_artifact_properties, driver, self._artifact_id, props)
        await _patch_chunks(collection, fetched, chunk_patch)

        self._emit(title, domain, sub_category, enriched=True)
        logger.info(
            "quick_capture_enrich.done artifact=%s domain=%s moved=%s title=%r",
            self._artifact_id, domain, moved, title,
        )
        return {"domain": domain, "moved": moved, "title": title, "sub_category": sub_category, "tags": tags}

    def _emit(self, title: str, domain: str, sub_category: str, *, enriched: bool) -> None:
        from utils.agent_events import emit_agent_event  # noqa: PLC0415

        message = f"Filed “{title}” under {domain}" if enriched else f"Saved “{title}” in {domain}"
        emit_agent_event(
            _AGENT, message,
            metadata={
                "artifact_id": self._artifact_id,
                "title": title,
                "domain": domain,
                "sub_category": sub_category,
                "enriched": enriched,
            },
        )


def _fetch_state(driver: Any, artifact_id: str) -> dict[str, Any] | None:
    """Synchronous Neo4j read — run in a thread."""
    with driver.session() as session:
        row = session.run(
            "MATCH (a:Artifact {id: $aid}) "
            "RETURN a.domain AS domain, a.filename AS filename, "
            "a.chunk_ids AS chunk_ids, a.quick_capture_enriched_at AS enriched_at",
            aid=artifact_id,
        ).single()
    return dict(row) if row else None


def _parse_ids(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(c) for c in raw]
    try:
        parsed = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(c) for c in parsed] if isinstance(parsed, list) else []


async def _patch_chunks(collection: Any, fetched: dict[str, Any], patch: dict[str, Any]) -> None:
    """Merge ``patch`` into every fetched chunk's metadata in place."""
    ids = fetched.get("ids") or []
    metas = fetched.get("metadatas") or []
    if not ids or not patch:
        return
    new_metas = [{**dict(m or {}), **patch} for m in metas]
    await asyncio.to_thread(lambda: collection.update(ids=ids, metadatas=new_metas))
