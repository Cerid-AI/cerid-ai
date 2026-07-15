# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Concrete BaseJob subclass for entity extraction.

Wraps the existing extraction pipeline:
  core.agents.entity_extraction.extract_entities_from_text  (LLM call)
  app.db.neo4j.entity.upsert_entities_for_artifact          (graph write)

The job fetches artifact chunks from ChromaDB, extracts entities via the
Ollama-backed LLM caller, and upserts them to Neo4j. It does NOT duplicate
any logic — it is purely a BaseJob-compliant front door that calls into
the existing service layer that ``scripts/backfill_entities.py`` already
exercises.

Payload schema
--------------
  {"artifact_id": str, "tenant_id": str}

``tenant_id`` is carried for multi-tenant future routing; the current
single-tenant stack uses it only for log correlation.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any

from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobResult, ProgressCallback
from core.processor.priority import Priority
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.entity_extraction")

# Typical token budget for the Ollama path (zero marginal USD cost).
# Extraction of an 8 000-char blob uses roughly 2 500 tokens in and
# 512 tokens out on the default llama3.1 model.
_EST_TOKENS_IN = 2_500
_EST_TOKENS_OUT = 512
_MODEL = "ollama/local"
_MAX_CHARS = 8_000

# Domain whose memory artifacts get bi-temporal :Fact derivation (Phase C). Chat
# transcripts live in the same domain but carry no ``memory_type`` — the fact
# step keys on a valid memory_type to restrict itself to extracted memories.
_CONVERSATIONS_DOMAIN = "conversations"


class EntityExtractionJob(BaseJob):
    """Extract and persist named entities for a single artifact.

    Calls the same pipeline as ``scripts/backfill_entities.py`` but
    surfaces it as a queueable job with progress callbacks so the
    processor pane can show live status.

    Parameters
    ----------
    artifact_id
        The Artifact node ID to process.
    tenant_id
        Tenant identifier (used for log correlation; single-tenant stacks
        may pass any stable string).
    """

    job_type = "entity_extraction"

    def __init__(self, artifact_id: str, tenant_id: str) -> None:
        self._artifact_id = artifact_id
        self._tenant_id = tenant_id

    @property
    def priority(self) -> Priority:
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        """Zero USD cost — entity extraction runs on the local Ollama path."""
        return CostEstimate(
            estimated_tokens_in=_EST_TOKENS_IN,
            estimated_tokens_out=_EST_TOKENS_OUT,
            model=_MODEL,
            estimated_usd=Decimal("0.00"),
            confidence="medium",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        """Execute the entity extraction pipeline for the configured artifact.

        Progress checkpoints
        --------------------
        0.0  — job started
        0.3  — chunks fetched from ChromaDB
        0.7  — LLM extraction complete
        1.0  — Neo4j upsert complete

        On any unhandled exception the error is logged via
        ``log_swallowed_error`` and re-raised so the worker can record
        the failure and schedule a retry. The job does not swallow errors;
        retry logic lives in the worker (Phase 3b).
        """
        await progress_cb(0.0)
        logger.info(
            "entity_extraction.start artifact=%s tenant=%s",
            self._artifact_id,
            self._tenant_id,
        )

        try:
            stats = await self._run_pipeline(progress_cb)
        except Exception as exc:
            log_swallowed_error(
                "processor.entity_extraction",
                exc,
                context={"artifact_id": self._artifact_id, "tenant_id": self._tenant_id},
            )
            raise

        return JobResult(
            job_id="",  # filled in by the worker after dequeue
            actual_tokens_in=_EST_TOKENS_IN,
            actual_tokens_out=_EST_TOKENS_OUT,
            metadata={
                "artifact_id": self._artifact_id,
                "tenant_id": self._tenant_id,
                "entities_upserted": stats.get("entities_upserted", 0),
                "edges_upserted": stats.get("edges_upserted", 0),
                "facts_written": stats.get("facts_written", 0),
                "skipped": stats.get("skipped"),
            },
        )

    # ------------------------------------------------------------------
    # Internal pipeline (keeps run() readable)
    # ------------------------------------------------------------------

    async def _run_pipeline(self, progress_cb: ProgressCallback) -> dict[str, Any]:
        """Fetch chunks, extract entities, upsert to graph."""
        from app.db.neo4j.entity import upsert_entities_for_artifact
        from app.deps import get_chroma, get_neo4j
        from config.taxonomy import collection_name
        from core.agents.entity_extraction import default_llm_caller, extract_entities_from_text

        driver = get_neo4j()
        chroma_client = get_chroma()

        # --- 1. Fetch chunks -------------------------------------------------
        domain = await asyncio.to_thread(
            self._fetch_domain, driver, self._artifact_id
        )
        if domain is None:
            logger.warning(
                "entity_extraction.artifact_not_found artifact=%s", self._artifact_id
            )
            return {"skipped": "artifact_not_found"}

        chunk_ids, docs, metadatas = await asyncio.to_thread(
            self._fetch_chunks, chroma_client, collection_name(domain), self._artifact_id
        )
        await progress_cb(0.3)

        if not chunk_ids:
            return {"entities_upserted": 0, "edges_upserted": 0, "skipped": "no_chunks"}

        blob = "\n\n---\n\n".join(d for d in docs if d)
        if not blob.strip():
            return {"entities_upserted": 0, "edges_upserted": 0, "skipped": "empty_text"}

        # --- 2. LLM extraction -----------------------------------------------
        entities = await extract_entities_from_text(
            blob,
            llm_caller=default_llm_caller,
            max_chars=_MAX_CHARS,
        )
        await progress_cb(0.7)

        if not entities:
            return {"entities_upserted": 0, "edges_upserted": 0, "skipped": "no_entities"}

        # --- 3. Neo4j upsert --------------------------------------------------
        stats = await asyncio.to_thread(
            upsert_entities_for_artifact,
            driver,
            self._artifact_id,
            entities,
            chunk_ids,
        )
        await progress_cb(1.0)

        logger.info(
            "entity_extraction.done artifact=%s entities=%d edges=%d",
            self._artifact_id,
            stats.get("entities_upserted", 0),
            stats.get("edges_upserted", 0),
        )

        # Phase K1.2 — emit entities_added event. The wiki refresh
        # subscriber (Phase K1.3) listens on this event and decides
        # which entities deserve a wiki page refresh based on
        # debounce + mention-count thresholds.
        try:
            from app.processor.event_hooks import emit  # noqa: PLC0415

            entity_slugs = [e.canonical_id for e in entities if getattr(e, "canonical_id", None)]
            emit("entities_added", {
                "artifact_id": self._artifact_id,
                "entity_slugs": entity_slugs,
                "tenant_id": self._tenant_id,
            })
        except Exception as exc:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "processor.entity_extraction.emit_event",
                exc,
                context={"artifact_id": self._artifact_id},
            )

        # Phase C — derive + write bi-temporal :Fact nodes for
        # conversation-derived memories. Reuses THIS job's already-resolved
        # entities (no extra LLM call). Gated + best-effort inside the helper.
        fact_stats = _derive_and_write_facts(
            driver,
            artifact_id=self._artifact_id,
            tenant_id=self._tenant_id,
            domain=domain,
            content=blob,
            chunk_metadatas=metadatas,
            entities=entities,
        )
        if fact_stats:
            stats["facts_written"] = fact_stats.get("facts_written", 0)

        return stats

    @staticmethod
    def _fetch_domain(driver: Any, artifact_id: str) -> str | None:
        """Synchronous Neo4j query — run in a thread."""
        with driver.session() as session:
            row = session.run(
                "MATCH (a:Artifact {id: $aid}) RETURN a.domain AS domain LIMIT 1",
                aid=artifact_id,
            ).single()
        return row["domain"] if row else None

    @staticmethod
    def _fetch_chunks(
        chroma_client: Any,
        coll_name: str,
        artifact_id: str,
    ) -> tuple[list[str], list[str], list[dict[str, Any]]]:
        """Synchronous ChromaDB query — run in a thread.

        Returns ``(chunk_ids, documents, metadatas)``. Metadatas carry the
        memory's bi-temporal fields (memory_type / event_date / valid_from /
        memory_source_type) that Phase-C fact derivation reads.
        """
        try:
            coll = chroma_client.get_collection(name=coll_name)
        except Exception:  # noqa: BLE001 — collection-missing is a valid skip path
            return [], [], []
        res = coll.get(
            where={"artifact_id": {"$eq": artifact_id}},
            include=["documents", "metadatas"],
        )
        return (
            list(res.get("ids", [])),
            list(res.get("documents", []) or []),
            list(res.get("metadatas", []) or []),
        )


# ---------------------------------------------------------------------------
# Phase C — bi-temporal :Fact derivation + write (fires inside this job so
# facts ride the entity job's single LLM pass; no second LLM call).
# ---------------------------------------------------------------------------


def _first_memory_metadata(metadatas: list[dict[str, Any]]) -> dict[str, Any]:
    """First non-empty chunk metadata. Memory chunks of one artifact share the
    bi-temporal fields, so the first is representative; empty dict when none."""
    for meta in metadatas:
        if meta:
            return meta
    return {}


def _derive_and_write_facts(
    driver: Any,
    *,
    artifact_id: str,
    tenant_id: str,
    domain: str,
    content: str,
    chunk_metadatas: list[dict[str, Any]],
    entities: list[Any],
) -> dict[str, int]:
    """Derive + persist bi-temporal :Fact nodes for a conversation-derived memory.

    Gated by ``ENABLE_FACT_WRITES`` and restricted to ``conversations``-domain
    memory artifacts (identified by a valid ``memory_type`` — chat transcripts
    in the same domain carry none). ``observation_date`` is threaded from the
    memory's already-stamped ``valid_from`` so each :Fact's valid-time start
    equals the Chroma memory's exactly (the two stores never diverge).

    Best-effort: any failure is logged and swallowed so the successful entity
    extraction is never lost.
    """
    from config.features import ENABLE_FACT_WRITES

    if not ENABLE_FACT_WRITES or domain != _CONVERSATIONS_DOMAIN:
        return {}

    from config.settings import MEMORY_TYPES

    meta = _first_memory_metadata(chunk_metadatas)
    memory_type = str(meta.get("memory_type", ""))
    if memory_type not in MEMORY_TYPES:
        return {}

    try:
        from app.db.neo4j.facts import write_facts
        from core.agents.fact_derivation import derive_facts

        facts = derive_facts(
            content=content,
            memory_type=memory_type,
            event_date=meta.get("event_date"),
            observation_date=meta.get("valid_from"),
            entity_ids=[
                e.canonical_id for e in entities if getattr(e, "canonical_id", None)
            ],
            memory_source_type=meta.get("memory_source_type"),
        )
        if not facts:
            return {"facts_written": 0}
        return write_facts(driver, facts, source_artifact_id=artifact_id)
    except Exception as exc:  # noqa: BLE001 — fact write is best-effort; never lose the entity extraction
        log_swallowed_error(
            "processor.entity_extraction.fact_write",
            exc,
            context={"artifact_id": artifact_id, "tenant_id": tenant_id},
        )
        return {}
