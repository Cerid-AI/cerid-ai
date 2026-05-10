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

        chunk_ids, docs = await asyncio.to_thread(
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
    ) -> tuple[list[str], list[str]]:
        """Synchronous ChromaDB query — run in a thread."""
        try:
            coll = chroma_client.get_collection(name=coll_name)
        except Exception:  # noqa: BLE001 — collection-missing is a valid skip path
            return [], []
        res = coll.get(
            where={"artifact_id": {"$eq": artifact_id}},
            include=["documents"],
        )
        return list(res.get("ids", [])), list(res.get("documents", []) or [])
