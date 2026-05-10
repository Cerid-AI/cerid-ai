# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Concrete BaseJob subclass for daily brief generation.

Runs the end-to-end daily brief pipeline:
  1. Assemble inbox + notes corpus via BriefService helpers.
  2. Synthesise via BriefService.generate_daily (Ollama-backed LLM).
  3. Persist the resulting BriefRecord to Neo4j via BriefService.store.

The BriefService instance is obtained through a module-level factory
``_get_brief_service()`` so tests can patch it without touching the job
class directly.

Payload schema (used by the worker registry for instantiation)
--------------------------------------------------------------
  {"target_date": "2026-05-10"}   # ISO-8601 date string
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobResult, ProgressCallback
from core.processor.priority import Priority
from core.utils.swallowed import log_swallowed_error

if TYPE_CHECKING:
    from app.services.briefs.service import BriefRecord, BriefService

logger = logging.getLogger("ai-companion.processor.brief_generation")

# ---------------------------------------------------------------------------
# Token budget — single LLM call with moderate context
# ---------------------------------------------------------------------------

_EST_TOKENS_IN = 6_000
_EST_TOKENS_OUT = 1_500
_MODEL = "ollama/local"


# ---------------------------------------------------------------------------
# Service factory — patched in tests
# ---------------------------------------------------------------------------


def _get_brief_service() -> "BriefService":
    """Return a default BriefService instance.

    Constructed lazily so the job module can be imported without a live
    database or LLM caller. Unit tests patch this function directly.
    """
    from app.services.briefs.service import BriefService

    return BriefService()


def _get_neo4j() -> Any:
    """Return the Neo4j driver singleton.

    Lazy accessor so the module is importable without a live database.
    Unit tests patch this function directly.
    """
    from app.deps import get_neo4j

    return get_neo4j()


# ---------------------------------------------------------------------------
# Corpus assembly helper (sync, run via asyncio.to_thread)
# ---------------------------------------------------------------------------


def _assemble_corpus(driver: Any, target_date: str) -> tuple[str, str]:
    """Query Neo4j for inbox items and recent notes for the given date.

    Returns (inbox_recent, notes_recent_7d) as plain strings.
    This is intentionally a cheap stub that pages through persisted
    :Brief and :Claim nodes. A richer implementation can replace
    this function once the inbox graph schema is finalised.
    """
    inbox_items: list[str] = []
    notes_items: list[str] = []

    try:
        with driver.session() as session:
            # Inbox: items created in last 24 h (approximated by generated_at)
            inbox_rows = session.run(
                """
                MATCH (b:Brief {kind: 'inbox'})
                WHERE b.generated_at >= datetime($target_date) - duration('P1D')
                RETURN b.sections AS sections
                ORDER BY b.generated_at DESC
                LIMIT 50
                """,
                target_date=target_date,
            ).data()
            for row in inbox_rows:
                if row.get("sections"):
                    inbox_items.append(str(row["sections"]))

            # Notes: recent vault entries from last 7 days
            notes_rows = session.run(
                """
                MATCH (c:Claim)
                WHERE c.created_at >= datetime($target_date) - duration('P7D')
                RETURN c.text AS text
                ORDER BY c.created_at DESC
                LIMIT 100
                """,
                target_date=target_date,
            ).data()
            for row in notes_rows:
                if row.get("text"):
                    notes_items.append(str(row["text"]))
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error(
            "processor.brief_generation.assemble_corpus",
            exc,
            context={"target_date": target_date},
        )

    return "\n\n".join(inbox_items), "\n\n".join(notes_items)


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


class BriefGenerationJob(BaseJob):
    """Generate and persist a daily brief for the given target date.

    Parameters
    ----------
    target_date
        ISO-8601 date string (e.g. ``"2026-05-10"``). The job assembles
        inbox content from the last 24 h and notes from the last 7 days
        relative to this date.
    """

    job_type = "brief_generation"

    def __init__(self, target_date: str) -> None:
        self._target_date = target_date

    @property
    def priority(self) -> Priority:
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        """Zero USD cost — brief generation uses the local Ollama path."""
        return CostEstimate(
            estimated_tokens_in=_EST_TOKENS_IN,
            estimated_tokens_out=_EST_TOKENS_OUT,
            model=_MODEL,
            estimated_usd=Decimal("0.00"),
            confidence="medium",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        """Execute the daily brief pipeline.

        Progress checkpoints
        --------------------
        0.0  — job started
        0.3  — corpus assembled (inbox + notes fetched from Neo4j)
        0.7  — LLM synthesis complete
        1.0  — BriefRecord persisted to Neo4j

        Exceptions propagate after logging so the worker can record the
        failure and schedule a retry.
        """
        await progress_cb(0.0)
        logger.info("brief_generation.start target_date=%s", self._target_date)

        try:
            result = await self._run_pipeline(progress_cb)
        except Exception as exc:
            log_swallowed_error(
                "processor.brief_generation",
                exc,
                context={"target_date": self._target_date},
            )
            raise

        return result

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    async def _run_pipeline(self, progress_cb: ProgressCallback) -> JobResult:
        """Assemble corpus, call LLM, persist record."""
        brief_service = _get_brief_service()
        driver = _get_neo4j()

        # --- 1. Assemble corpus (synchronous Neo4j queries) ----------------
        inbox_recent, notes_recent_7d = await asyncio.to_thread(
            _assemble_corpus, driver, self._target_date
        )
        await progress_cb(0.3)

        # --- 2. LLM synthesis ----------------------------------------------
        record: "BriefRecord" = await brief_service.generate_daily(
            inbox_recent,
            notes_recent_7d,
        )
        await progress_cb(0.7)

        # --- 3. Persist to Neo4j ------------------------------------------
        await brief_service.store(record, driver)
        await progress_cb(1.0)

        logger.info(
            "brief_generation.done target_date=%s brief_id=%s status=%s",
            self._target_date,
            record.brief_id,
            record.status,
        )

        return JobResult(
            job_id="",  # filled in by the worker after dequeue
            actual_tokens_in=_EST_TOKENS_IN,
            actual_tokens_out=_EST_TOKENS_OUT,
            metadata={
                "target_date": self._target_date,
                "brief_id": record.brief_id,
                "status": record.status,
            },
        )
