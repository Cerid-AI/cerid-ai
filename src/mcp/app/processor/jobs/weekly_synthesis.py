# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Concrete BaseJob subclass for weekly synthesis brief generation.

Runs the end-to-end weekly synthesis pipeline:
  1. Build a vault snapshot from recent :Brief and :Claim nodes (Neo4j).
  2. Pull contradiction findings from the last 7 days.
  3. Synthesise via BriefService.generate_weekly (Ollama-backed LLM).
  4. Persist the resulting BriefRecord to Neo4j via BriefService.store.
  5. (RAG C3.4) Optionally write the synthesis markdown back to a
     registered vault as ``_briefs/synthesis-YYYY-MM-DD.md`` via
     ``vault_write.write_note``.  Opt-in per job; vault failures are
     swallowed so they cannot fail an otherwise-successful synthesis.

The BriefService is obtained through ``_get_brief_service()`` so tests
can patch it without modifying the job class.

Payload schema (used by the worker registry for instantiation)
--------------------------------------------------------------
  {
    "week_ending": "2026-05-11",          # ISO-8601 date string
    "write_to_vault": false,              # opt-in vault writeback (C3.4)
    "vault_id": null,                     # required when write_to_vault=True
    "vault_folder": "_briefs",            # path prefix under the vault root
  }
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


def _get_chroma() -> Any:
    """Return the ChromaDB client singleton.

    Lazy accessor so the module is importable without a live vector
    store. Unit tests patch this function directly.
    """
    from app.deps import get_chroma

    return get_chroma()


def _get_redis() -> Any:
    """Return the Redis client singleton.

    Lazy accessor so the module is importable without a live cache.
    Unit tests patch this function directly.
    """
    from app.deps import get_redis

    return get_redis()


# ---------------------------------------------------------------------------
# Vault snapshot helper (sync, run via asyncio.to_thread)
# ---------------------------------------------------------------------------


def _render_synthesis_markdown(record: "BriefRecord", week_ending: str) -> str:
    """Render a weekly ``BriefRecord`` as a markdown body for a vault note.

    Mirrors :func:`brief_generation._render_brief_markdown` so the two
    flows produce the same shape of note (top-level heading + one ``##``
    section per parsed section).  Kept inline here rather than shared
    in a helper module because the heading wording differs ("Daily Brief"
    vs "Weekly Synthesis") and the function is small.
    """
    lines: list[str] = [f"# Weekly Synthesis — week ending {week_ending}", ""]
    sections = record.sections or {}
    if not sections:
        lines.append(f"_status_: **{record.status}**")
        lines.append("")
        lines.append("_No sections were parsed from the LLM output._")
        return "\n".join(lines) + "\n"

    for name, body in sections.items():
        lines.append(f"## {name}")
        lines.append("")
        lines.append(body.strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _vault_write_synthesis(
    *,
    week_ending: str,
    vault_id: str,
    vault_folder: str,
    record: "BriefRecord",
) -> bool:
    """Write the rendered synthesis markdown to a registered vault.

    Forgiving by design — uses ``mode="append"`` so a re-run for the
    same week stacks rather than overwriting.  ``allow_synthesis_input``
    is hard-coded ``False``: the weekly synthesis MUST NOT feed back
    into the next synthesis run's input set.

    Returns True on success, False on failure. All exceptions are
    swallowed via ``log_swallowed_error`` so the synthesis job never
    fails on vault-write errors; the bool return lets the caller
    surface the real outcome in JobResult.metadata.
    """
    from app.deps import get_redis
    from app.services.vault_write import WriteNoteRequest, write_note

    try:
        body = _render_synthesis_markdown(record, week_ending)
        rel_path = f"{vault_folder.rstrip('/')}/synthesis-{week_ending}.md"
        write_note(
            WriteNoteRequest(
                vault_id=vault_id,
                path=rel_path,
                content=body,
                frontmatter={
                    "cerid:job_type": "weekly_synthesis",
                    "cerid:week_ending": week_ending,
                },
                mode="append",
                allow_synthesis_input=False,
            ),
            get_redis(),
        )
        logger.info(
            "weekly_synthesis.vault_write_ok week_ending=%s vault_id=%s path=%s",
            week_ending, vault_id, rel_path,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "processor.weekly_synthesis.vault_write",
            exc,
            context={
                "week_ending": week_ending,
                "vault_id": vault_id,
                "vault_folder": vault_folder,
            },
        )
        return False


def _build_vault_snapshot(driver: Any, week_ending: str) -> str:
    """Page through Neo4j for recent Brief + Claim summaries.

    Returns a serialised text blob suitable for the weekly prompt.
    Pages through (:Brief) and (:Claim) nodes from the last 7 days
    relative to ``week_ending``.

    RAG C3.3 loop-breaker
    ---------------------
    Claims whose upstream Artifact carries ``source_type="cerid-synthesis"``
    are excluded by default — otherwise last week's synthesis would
    contaminate this week's input set and Cerid would amplify its own
    outputs.  Claims with ``cerid_reanalyze=true`` on the source
    Artifact re-enter the input set for the "reconsider this synthesis
    with new evidence" carve-out.  The filter uses OPTIONAL MATCH so
    orphan Claims (no linked Artifact) pass through unchanged.
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

            # Recent claims / vault notes — see module docstring for the
            # cerid-synthesis loop-breaker rationale.
            claim_rows = session.run(
                """
                MATCH (c:Claim)
                WHERE c.created_at >= datetime($week_ending) - duration('P7D')
                OPTIONAL MATCH (c)-[:EXTRACTED_FROM]->(a:Artifact)
                WITH c, a
                WHERE a IS NULL
                   OR coalesce(a.source_type, '') <> 'cerid-synthesis'
                   OR coalesce(a.cerid_reanalyze, false) = true
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
# Claim verification (Task 2.1b — best-effort, never fails the job)
# ---------------------------------------------------------------------------


async def _verify_and_persist_claims(
    driver: Any,
    record: "BriefRecord",
    week_ending: str,
) -> list[dict[str, Any]]:
    """Best-effort claim-verification pass.

    Mirrors ``brief_generation._verify_and_persist_claims`` — runs claim
    extraction + KB verification against the synthesis's parsed sections
    and persists a trust band per claim. Any failure is swallowed via
    ``log_swallowed_error``; the synthesis must never fail because
    verification failed. Returns ``[]`` on any failure or when no claims
    were surfaced, in which case the brief is still stored with
    ``claim_ids=[]``.
    """
    from app.db.neo4j.briefs import save_verified_claims
    from app.services.briefs.verification import verify_brief_claims

    try:
        claims = await verify_brief_claims(
            record.sections,
            chroma_client=_get_chroma(),
            neo4j_driver=driver,
            redis_client=_get_redis(),
        )
        if claims:
            await asyncio.to_thread(save_verified_claims, driver, claims)
        return claims
    except Exception as exc:  # noqa: BLE001 — verification is best-effort by design
        log_swallowed_error(
            "processor.weekly_synthesis.verify_claims",
            exc,
            context={"week_ending": week_ending},
        )
        return []


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
    write_to_vault
        RAG C3.4. When True and ``vault_id`` is set, the generated
        synthesis markdown is written back to the named vault at
        ``{vault_folder}/synthesis-{week_ending}.md`` after the brief is
        persisted to Neo4j.  Defaults to False — opt-in per job.
    vault_id
        Target vault (watched-folder ID with ``is_vault=True``).  Required
        when ``write_to_vault`` is True; ignored otherwise.
    vault_folder
        Path prefix under the vault root.  Defaults to ``"_briefs"``.
    """

    job_type = "weekly_synthesis"

    def __init__(
        self,
        week_ending: str,
        *,
        write_to_vault: bool = False,
        vault_id: str | None = None,
        vault_folder: str | None = None,
    ) -> None:
        self._week_ending = week_ending
        self._write_to_vault = bool(write_to_vault)
        self._vault_id = vault_id
        self._vault_folder = vault_folder or "_briefs"

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

        # --- 3.5 (Task 2.1b) Best-effort claim verification ----------------
        claims = await _verify_and_persist_claims(driver, record, self._week_ending)
        if claims:
            record = record.model_copy(
                update={"claim_ids": [c["claim_id"] for c in claims]}
            )

        # --- 4. Persist to Neo4j ------------------------------------------
        await brief_service.store(record, driver)

        # --- 5. (RAG C3.4) Optional vault writeback -----------------------
        vault_written = False
        if self._write_to_vault and self._vault_id:
            vault_written = await asyncio.to_thread(
                _vault_write_synthesis,
                week_ending=self._week_ending,
                vault_id=self._vault_id,
                vault_folder=self._vault_folder,
                record=record,
            )

        await progress_cb(1.0)

        logger.info(
            "weekly_synthesis.done week_ending=%s brief_id=%s status=%s "
            "contradictions=%d vault_written=%s",
            self._week_ending,
            record.brief_id,
            record.status,
            len(contradictions),
            vault_written,
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
                "vault_written": vault_written,
            },
        )
