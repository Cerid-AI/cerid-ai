# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Entity extraction for ``:Memory`` nodes (episodic-memory graph linkage).

The episodic-memory counterpart of :mod:`app.processor.jobs.entity_extraction`.
Wraps the same pipeline:

  core.agents.entity_extraction.extract_entities_from_text  (LLM call)
  app.db.neo4j.entity.upsert_entities_for_memory            (graph write)

Why a separate job rather than reusing ``EntityExtractionJob``: that job
resolves an artifact's ``domain``, then fetches the artifact's chunked
documents out of ChromaDB to build its extraction blob. A ``:Memory`` node has
neither — its text lives inline on the node — so the fetch half does not apply
and the write targets a different edge source.

Background: memories reach the graph by two different paths. Conversational
memories are stored as ``:Artifact`` nodes and already enqueue
``EntityExtractionJob`` (Phase K2.1, ``core.agents.memory``). Verified-claim
promotion (``core.agents.verified_memory.promote_verified_facts``) instead
creates ``:Memory`` nodes, and never enqueued anything — so that surface
carried zero entity edges. This job closes that gap.

Payload schema
--------------
  {"memory_id": str, "tenant_id": str}
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

logger = logging.getLogger("ai-companion.processor.memory_entity_extraction")

# Memory text is short (a single promoted claim), so the budget is well under
# the artifact job's. Zero marginal USD — local Ollama path.
_EST_TOKENS_IN = 600
_EST_TOKENS_OUT = 256
_MODEL = "ollama/local"
_MAX_CHARS = 8_000


class MemoryEntityExtractionJob(BaseJob):
    """Extract and persist named entities for a single ``:Memory`` node."""

    job_type = "memory_entity_extraction"

    def __init__(self, memory_id: str, tenant_id: str) -> None:
        self._memory_id = memory_id
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
        """Execute the extraction pipeline for the configured memory.

        Progress checkpoints
        --------------------
        0.0  — job started
        0.3  — memory text read from Neo4j
        0.7  — LLM extraction complete
        1.0  — Neo4j upsert complete

        Errors are logged then re-raised so the worker records the failure and
        schedules a retry; retry logic lives in the worker, not here.
        """
        await progress_cb(0.0)
        logger.info(
            "memory_entity_extraction.start memory=%s tenant=%s",
            self._memory_id,
            self._tenant_id,
        )

        try:
            stats = await self._run_pipeline(progress_cb)
        except Exception as exc:
            log_swallowed_error(
                "processor.memory_entity_extraction",
                exc,
                context={"memory_id": self._memory_id, "tenant_id": self._tenant_id},
            )
            raise

        return JobResult(
            job_id="",  # filled in by the worker after dequeue
            actual_tokens_in=_EST_TOKENS_IN,
            actual_tokens_out=_EST_TOKENS_OUT,
            metadata={
                "memory_id": self._memory_id,
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
        """Read the memory text, extract entities, upsert MENTIONS edges."""
        from app.db.neo4j.entity import upsert_entities_for_memory
        from app.deps import get_neo4j
        from core.agents.entity_extraction import default_llm_caller, extract_entities_from_text

        driver = get_neo4j()

        text = await asyncio.to_thread(self._fetch_text, driver, self._memory_id)
        await progress_cb(0.3)

        if text is None:
            logger.warning(
                "memory_entity_extraction.memory_not_found memory=%s", self._memory_id
            )
            return {"skipped": "memory_not_found"}
        if not text.strip():
            return {"entities_upserted": 0, "edges_upserted": 0, "skipped": "empty_text"}

        entities = await extract_entities_from_text(
            text,
            llm_caller=default_llm_caller,
            max_chars=_MAX_CHARS,
        )
        await progress_cb(0.7)

        if not entities:
            return {"entities_upserted": 0, "edges_upserted": 0, "skipped": "no_entities"}

        stats = await asyncio.to_thread(
            upsert_entities_for_memory,
            driver,
            self._memory_id,
            entities,
        )
        await progress_cb(1.0)

        logger.info(
            "memory_entity_extraction.done memory=%s entities=%d edges=%d",
            self._memory_id,
            stats.get("entities_upserted", 0),
            stats.get("edges_upserted", 0),
        )
        return stats

    @staticmethod
    def _fetch_text(driver, memory_id: str) -> str | None:
        """Return the memory's text, or ``None`` when the node is absent."""
        with driver.session() as session:
            row = session.run(
                "MATCH (m:Memory {id: $mid}) RETURN m.text AS text",
                mid=memory_id,
            ).single()
        if row is None:
            return None
        return row["text"] or ""
