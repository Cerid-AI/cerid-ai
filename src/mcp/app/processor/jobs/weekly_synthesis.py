# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Concrete BaseJob subclass for weekly synthesis brief generation.

Runs the end-to-end weekly synthesis pipeline:
  1. Build a vault snapshot from recent :Brief and :Claim nodes (Neo4j).
  2. Pull contradiction findings from the last 7 days.
  3. Synthesise via BriefService.generate_weekly (Ollama-backed LLM).
  4. Persist the resulting BriefRecord to Neo4j via BriefService.store.

The BriefService is obtained through ``_get_brief_service()`` so tests
can patch it without modifying the job class.

Payload schema (used by the worker registry for instantiation)
--------------------------------------------------------------
  {"week_ending": "2026-05-11"}   # ISO-8601 date string (Sunday or Monday)
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
    from app.services.contradiction_log import ContradictionFinding

logger = logging.getLogger("ai-companion.processor.weekly_synthesis")

# ---------------------------------------------------------------------------
# Token budget — larger than daily: reads the full vault snapshot
# ---------------------------------------------------------------------------

_EST_TOKENS_IN = 12_000
_EST_TOKENS_OUT = 2_500
_MODEL = "ollama/local"


# ---------------------------------------------------------------------------
# Service factory — patched in tests
# ---------------------------------------------------------------------------


def _get_brief_service() -> "BriefService":
    """Return a default BriefService instance.

    Constructed lazily so the module can be imported without live infra.
    Unit tests patch this function directly.
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
# Vault snapshot helper (sync, run via asyncio.to_thread)
# ---------------------------------------------------------------------------


def _build_vault_snapshot(driver: Any, week_ending: str) -> str:
    """Page through Neo4j for recent Brief + Claim summaries.

    Returns a serialised text blob suitable for the weekly prompt.
    Pages through (:Brief) and (:Claim) nodes from the last 7 days
    relative to ``week_ending``.
    """
    lines: list[str] = []

    try:
        with driver.session() as session:
            # Recent non-inbox briefs (daily summaries)
            brief_rows = session.run(
                """
                MATCH (b:Brief)
                WHERE b.kind IN ['daily', 'weekly']
                  AND b.generated_at >= datetime($week_ending) - duration('P7D')
                RETURN b.brief_id AS brief_id, b.kind AS kind,
                       b.sections AS sections
                ORDER BY b.generated_at DESC
                LIMIT 30
                """,
                week_ending=week_ending,
            ).data()
            for row in brief_rows:
                lines.append(
                    f"[{row.get('kind', 'brief').upper()} {row.get('brief_id', '')}] "
                    f"{str(row.get('sections', ''))[:500]}"
                )

            # Recent claims / vault notes
            claim_rows = session.run(
                """
                MATCH (c:Claim)
                WHERE c.created_at >= datetime($week_ending) - duration('P7D')
                RETURN c.text AS text, c.claim_id AS claim_id
                ORDER BY c.created_at DESC
                LIMIT 150
                """,
                week_ending=week_ending,
            ).data()
            for row in claim_rows:
                text = (row.get("text") or "").strip()
                if text:
                    lines.append(f"[CLAIM {row.get('claim_id', '')}] {text[:300]}")
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error(
            "processor.weekly_synthesis.build_vault_snapshot",
            exc,
            context={"week_ending": week_ending},
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


class WeeklySynthesisJob(BaseJob):
    """Generate and persist a weekly synthesis brief for the given week.

    Parameters
    ----------
    week_ending
        ISO-8601 date string for the Sunday or Monday the week ends
        (e.g. ``"2026-05-11"``). Used to bound vault snapshot and
        contradiction queries to the preceding 7 days.
    """

    job_type = "weekly_synthesis"

    def __init__(self, week_ending: str) -> None:
        self._week_ending = week_ending

    @property
    def priority(self) -> Priority:
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        """Zero USD cost — weekly synthesis uses the local Ollama path."""
        return CostEstimate(
            estimated_tokens_in=_EST_TOKENS_IN,
            estimated_tokens_out=_EST_TOKENS_OUT,
            model=_MODEL,
            estimated_usd=Decimal("0.00"),
            confidence="medium",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        """Execute the weekly synthesis pipeline.

        Progress checkpoints
        --------------------
        0.0  — job started
        0.2  — vault snapshot assembled (recent :Brief + :Claim from Neo4j)
        0.4  — contradiction log queried (last 7 days)
        0.7  — LLM synthesis complete
        1.0  — BriefRecord persisted to Neo4j

        Exceptions propagate after logging so the worker can record the
        failure and schedule a retry.
        """
        await progress_cb(0.0)
        logger.info("weekly_synthesis.start week_ending=%s", self._week_ending)

        try:
            result = await self._run_pipeline(progress_cb)
        except Exception as exc:
            log_swallowed_error(
                "processor.weekly_synthesis",
                exc,
                context={"week_ending": self._week_ending},
            )
            raise

        return result

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    async def _run_pipeline(self, progress_cb: ProgressCallback) -> JobResult:
        """Assemble snapshot + contradictions, call LLM, persist record."""
        from app.services import contradiction_log as _cl

        brief_service = _get_brief_service()
        driver = _get_neo4j()

        # --- 1. Vault snapshot (synchronous Neo4j queries) -----------------
        full_vault_snapshot = await asyncio.to_thread(
            _build_vault_snapshot, driver, self._week_ending
        )
        await progress_cb(0.2)

        # --- 2. Contradiction log (last 7 days) ----------------------------
        # Compute an ISO lower bound: week_ending minus 7 days.
        # We keep this simple to avoid importing datetime at module level.
        from datetime import date, timedelta

        try:
            week_end_date = date.fromisoformat(self._week_ending)
            since_iso = (week_end_date - timedelta(days=7)).isoformat()
        except ValueError:
            since_iso = None  # fall back to no lower bound

        contradictions: list["ContradictionFinding"] = []
        try:
            contradictions = await _cl.list_recent(since=since_iso, limit=50)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(
                "processor.weekly_synthesis.contradiction_query",
                exc,
                context={"week_ending": self._week_ending},
            )

        contradiction_log_text = "\n".join(
            f"[{f.severity.upper()}] {f.claim_a_text} ↔ {f.claim_b_text}"
            for f in contradictions
        )
        await progress_cb(0.4)

        # --- 3. LLM synthesis ---------------------------------------------
        week_window = f"{since_iso or '?'} – {self._week_ending}"
        record: "BriefRecord" = await brief_service.generate_weekly(
            full_vault_snapshot,
            contradiction_log_text,
            week_window=week_window,
        )
        await progress_cb(0.7)

        # --- 4. Persist to Neo4j ------------------------------------------
        await brief_service.store(record, driver)
        await progress_cb(1.0)

        logger.info(
            "weekly_synthesis.done week_ending=%s brief_id=%s status=%s contradictions=%d",
            self._week_ending,
            record.brief_id,
            record.status,
            len(contradictions),
        )

        return JobResult(
            job_id="",  # filled in by the worker after dequeue
            actual_tokens_in=_EST_TOKENS_IN,
            actual_tokens_out=_EST_TOKENS_OUT,
            metadata={
                "week_ending": self._week_ending,
                "brief_id": record.brief_id,
                "status": record.status,
                "contradictions_included": len(contradictions),
            },
        )
