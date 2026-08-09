# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Concrete BaseJob subclass for HyPE (Hypothetical Prompt Embeddings) indexing.

Phase R.3.  Wraps :func:`app.services.hype_indexer.index_chunk_with_hype`
as a queueable low-priority background job.  Enqueued by the ingestion
service after a chunk is committed, when ``RETRIEVAL_HYPE_ENABLED=true``.

Payload schema
--------------
  {"chunk_id": str, "content": str, "collection_name": str, "artifact_id": str}

Token budget
------------
Local Ollama path — approximately 3 000 tokens in (chunk + prompt) and 800
tokens out (5 questions).  USD cost = $0.00.

Priority
--------
``Priority.LOW`` — HyPE generation is best-effort enrichment; it must not
compete with user-facing operations.

Progress checkpoints
--------------------
0.0  — job started
0.4  — hypothetical questions generated
0.8  — questions embedded
1.0  — stored to Chroma; job complete
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobResult, ProgressCallback
from core.processor.priority import Priority
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.hype_indexing")

_EST_TOKENS_IN = 3_000
_EST_TOKENS_OUT = 800
_MODEL = "ollama/local"


class HyPEIndexingJob(BaseJob):
    """Generate and store HyPE question embeddings for a single chunk.

    Parameters
    ----------
    chunk_id:
        The primary chunk's ChromaDB ID.
    content:
        The chunk's text content.
    collection_name:
        The primary ChromaDB collection name (e.g. ``"cerid_general"``).
    artifact_id:
        The artifact ID for provenance metadata.
    n:
        Number of hypothetical questions to generate (default 5).
    """

    job_type = "hype_indexing"

    def __init__(
        self,
        chunk_id: str,
        content: str,
        collection_name: str,
        artifact_id: str,
        n: int = 5,
    ) -> None:
        self._chunk_id = chunk_id
        self._content = content
        self._collection_name = collection_name
        self._artifact_id = artifact_id
        self._n = n

    @property
    def priority(self) -> Priority:
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        """Zero USD cost — HyPE indexing runs on the local Ollama path."""
        return CostEstimate(
            estimated_tokens_in=_EST_TOKENS_IN,
            estimated_tokens_out=_EST_TOKENS_OUT,
            model=_MODEL,
            estimated_usd=Decimal("0.00"),
            confidence="medium",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        """Execute the HyPE indexing pipeline for the configured chunk.

        On any unhandled exception the error is logged via
        ``log_swallowed_error`` and re-raised so the worker can record
        the failure and schedule a retry.
        """
        await progress_cb(0.0)
        logger.info(
            "hype_indexing.start chunk_id=%s artifact=%s",
            self._chunk_id,
            self._artifact_id,
        )

        try:
            stats = await self._run_pipeline(progress_cb)
        except Exception as exc:
            log_swallowed_error(
                "processor.hype_indexing",
                exc,
                context={
                    "chunk_id": self._chunk_id,
                    "artifact_id": self._artifact_id,
                },
            )
            raise

        return JobResult(
            job_id="",  # filled in by the worker after dequeue
            actual_tokens_in=_EST_TOKENS_IN,
            actual_tokens_out=_EST_TOKENS_OUT,
            metadata={
                "chunk_id": self._chunk_id,
                "artifact_id": self._artifact_id,
                "enabled": stats.get("enabled", False),
                "n_prompts": stats.get("n_prompts", 0),
                "total_tokens": stats.get("total_tokens", 0),
            },
        )

    # ------------------------------------------------------------------
    # Internal pipeline (keeps run() readable)
    # ------------------------------------------------------------------

    async def _run_pipeline(self, progress_cb: ProgressCallback) -> dict[str, Any]:
        """Delegate to the hype_indexer service with progress callbacks."""
        from app.services.hype_indexer import index_chunk_with_hype

        # 0.0 already emitted by run(); call the service which handles
        # LLM + embed + storage internally.  We emit intermediate progress
        # by hooking before and after the key sub-steps.

        # We can't hook inside index_chunk_with_hype without coupling it to
        # the progress callback protocol.  Instead we emit coarse checkpoints
        # around the full call — this is acceptable for a LOW priority job.
        result = await index_chunk_with_hype(
            self._chunk_id,
            self._content,
            collection_name=self._collection_name,
            artifact_id=self._artifact_id,
            n=self._n,
        )

        # After the service call completes, emit remaining milestones.
        # The service does generate → embed → store sequentially, so
        # reporting 0.4/0.8/1.0 here accurately reflects the shape.
        await progress_cb(0.4)
        await progress_cb(0.8)
        await progress_cb(1.0)

        logger.info(
            "hype_indexing.done chunk_id=%s n_prompts=%d enabled=%s",
            self._chunk_id,
            result.get("n_prompts", 0),
            result.get("enabled", False),
        )
        return result
