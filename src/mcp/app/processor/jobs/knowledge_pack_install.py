# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""KnowledgePackInstallJob — queued knowledge-pack install.

Pack installs used to run synchronously inside the POST
``/knowledge_packs/{pack_id}/install`` request. Three concurrent installs
plus backlog drain OOM'd the mcp container mid-beta (2026-07-12 triage),
502-ing every in-flight wizard request. Wrapping the install in a
``BaseJob`` moves the download → verify → extract → ingest pipeline onto
the background processor, where the load throttle and the single
``_install_lock`` in :mod:`app.services.knowledge_packs` serialise the
work.

Idempotency: :func:`app.services.knowledge_packs.install_pack` is a no-op
when the same id+version is already installed, and re-runs dedup cleanly
at the artifact level via content_hash — so a retried or double-enqueued
job converges instead of duplicating content.

Discovered automatically by ``build_default_registry()``.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from core.knowledge.packs import PackError
from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobResult, ProgressCallback
from core.processor.priority import Priority

logger = logging.getLogger("ai-companion.processor.knowledge_pack_install")

# No direct LLM call — ingest-time enrichment is billed by the ingestion
# pipeline's own jobs, not this orchestration wrapper.
_EST_TOKENS_IN = 0
_EST_TOKENS_OUT = 0
_MODEL = "none"


class KnowledgePackInstallJob(BaseJob):
    """Install a knowledge pack by registry id.

    Parameters
    ----------
    pack_id
        Registry id of the pack to install. The registry is re-read at
        run time so the job survives a server restart between enqueue
        and execution.
    """

    job_type = "knowledge_pack_install"

    def __init__(self, pack_id: str) -> None:
        self._pack_id = pack_id

    @property
    def priority(self) -> Priority:
        # User-triggered from the setup wizard / knowledge library — the
        # user is actively waiting on the "installing" badge to flip.
        return Priority.HIGH

    def estimate_cost(self) -> CostEstimate:
        return CostEstimate(
            estimated_tokens_in=_EST_TOKENS_IN,
            estimated_tokens_out=_EST_TOKENS_OUT,
            model=_MODEL,
            estimated_usd=Decimal("0.00"),
            confidence="high",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        # Lazy imports: keep job discovery (build_default_registry imports
        # this module at startup) free of the app/services wiring.
        from app.services.knowledge_packs import (
            default_registry_path,
            install_pack_default,
        )
        from core.knowledge.packs import load_registry

        await progress_cb(0.0)
        registry = load_registry(default_registry_path())
        pack = registry.get(self._pack_id)
        if pack is None:
            raise PackError(f"Pack {self._pack_id!r} not in registry")

        logger.info("knowledge_pack_install.start pack=%s@%s", pack.id, pack.version)
        record = await install_pack_default(pack)
        await progress_cb(1.0)
        logger.info(
            "knowledge_pack_install.done pack=%s@%s artifacts=%d",
            record.pack_id, record.version, len(record.artifact_ids),
        )
        return JobResult(
            job_id="",  # filled in by the worker after dequeue
            actual_tokens_in=0,
            actual_tokens_out=0,
            metadata={
                "pack_id": record.pack_id,
                "version": record.version,
                "domain": record.domain,
                "artifact_count": len(record.artifact_ids),
            },
        )
