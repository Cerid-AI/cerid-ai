# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Concrete BaseJob subclass for daily brief generation.

Runs the end-to-end daily brief pipeline:
  1. Assemble inbox + notes corpus via BriefService helpers.
  2. Synthesise via BriefService.generate_daily (Ollama-backed LLM).
  3. Persist the resulting BriefRecord to Neo4j via BriefService.store.
  4. (RAG C3.4) Optionally write the brief markdown back to a registered
     vault as ``_briefs/brief-YYYY-MM-DD.md`` via ``vault_write.write_note``.
     OPT-IN per job via ``write_to_vault=True`` on the payload — the
     default scheduler does NOT enable this. Vault-write failures are
     swallowed and logged, NEVER propagated, because the brief is
     already persisted in Neo4j and a vault-write failure shouldn't
     mark an otherwise-successful job as failed.

The BriefService instance is obtained through a module-level factory
``_get_brief_service()`` so tests can patch it without touching the job
class directly.

Payload schema (used by the worker registry for instantiation)
--------------------------------------------------------------
  {
    "target_date": "2026-05-10",          # ISO-8601 date string
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
# Corpus assembly helper (sync, run via asyncio.to_thread)
# ---------------------------------------------------------------------------


def _render_brief_markdown(record: "BriefRecord", target_date: str) -> str:
    """Render a ``BriefRecord`` to a markdown body suitable for a vault note.

    Each parsed section is rendered as a ``## SECTION_NAME`` block,
    preserving the order they appeared in the original LLM output.
    The top-level ``# Daily Brief — {date}`` heading anchors the file
    so the vault renders it cleanly.
    """
    lines: list[str] = [f"# Daily Brief — {target_date}", ""]
    sections = record.sections or {}
    if not sections:
        # Empty / failed-parse fallback — record the status so the
        # vault note still reflects what happened.
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


def _vault_write_brief(
    *,
    target_date: str,
    vault_id: str,
    vault_folder: str,
    record: "BriefRecord",
) -> bool:
    """Write the rendered brief markdown back to a registered vault.

    Idempotent / forgiving by design: uses ``mode="append"`` so re-runs
    on the same date stack additional sections onto an existing file
    rather than overwriting earlier output.  ``allow_synthesis_input``
    is hard-coded ``False`` — briefs MUST NOT feed back into the next
    brief's input set (loop-breaker).

    Returns True on success, False on failure. All exceptions are
    swallowed via ``log_swallowed_error`` so a vault write failure
    cannot fail a brief job that's otherwise complete; the bool
    return lets the caller log the real outcome in JobResult.metadata
    instead of always reporting vault_written=True.
    """
    from app.deps import get_redis
    from app.services.vault_write import WriteNoteRequest, write_note

    try:
        body = _render_brief_markdown(record, target_date)
        rel_path = f"{vault_folder.rstrip('/')}/brief-{target_date}.md"
        write_note(
            WriteNoteRequest(
                vault_id=vault_id,
                path=rel_path,
                content=body,
                frontmatter={
                    "cerid:job_type": "brief_generation",
                    "cerid:target_date": target_date,
                },
                mode="append",
                allow_synthesis_input=False,
            ),
            get_redis(),
        )
        logger.info(
            "brief_generation.vault_write_ok target_date=%s vault_id=%s path=%s",
            target_date, vault_id, rel_path,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "processor.brief_generation.vault_write",
            exc,
            context={
                "target_date": target_date,
                "vault_id": vault_id,
                "vault_folder": vault_folder,
            },
        )
        return False


def _assemble_corpus(driver: Any, target_date: str) -> tuple[str, str]:
    """Query Neo4j for inbox items and recent notes for the given date.

    Returns (inbox_recent, notes_recent_7d) as plain strings.
    This is intentionally a cheap stub that pages through persisted
    :Brief and :Claim nodes. A richer implementation can replace
    this function once the inbox graph schema is finalised.

    RAG C3.3 loop-breaker
    ---------------------
    Claims sourced from a Cerid-authored note (``source_type="cerid-synthesis"``
    on the upstream Artifact) are excluded by default — otherwise this
    job's own outputs would feed back into tomorrow's brief.  Notes
    that explicitly opt back in via ``cerid_reanalyze=true`` are
    re-included so the carve-out for "consider this updated synthesis"
    workflows still works.  The filter uses an OPTIONAL MATCH so claims
    with no linked Artifact (legacy / direct-ingest) pass through
    unchanged.
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

            # Notes: recent vault entries from last 7 days.  The OPTIONAL
            # MATCH lets the loop-breaker filter consider the upstream
            # Artifact's source_type without requiring every Claim to
            # have a linked Artifact — orphan / direct-ingest claims
            # (no EXTRACTED_FROM edge) pass through unchanged.
            notes_rows = session.run(
                """
                MATCH (c:Claim)
                WHERE c.created_at >= datetime($target_date) - duration('P7D')
                OPTIONAL MATCH (c)-[:EXTRACTED_FROM]->(a:Artifact)
                WITH c, a
                WHERE a IS NULL
                   OR coalesce(a.source_type, '') <> 'cerid-synthesis'
                   OR coalesce(a.cerid_reanalyze, false) = true
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
# Claim verification (Task 2.1b — best-effort, never fails the job)
# ---------------------------------------------------------------------------


async def _verify_and_persist_claims(
    driver: Any,
    record: "BriefRecord",
    target_date: str,
) -> list[dict[str, Any]]:
    """Best-effort claim-verification pass.

    Runs claim extraction + KB verification against the brief's parsed
    sections and persists a trust band per claim. Any failure — Chroma
    down, LLM error, extraction error — is swallowed via
    ``log_swallowed_error``; brief generation must never fail because
    verification failed. Returns ``[]`` on any failure or when no
    claims were surfaced, in which case the brief is still stored with
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
            "processor.brief_generation.verify_claims",
            exc,
            context={"target_date": target_date},
        )
        return []


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
    write_to_vault
        RAG C3.4. When True and ``vault_id`` is set, the generated brief
        markdown is written back to the named vault at
        ``{vault_folder}/brief-{target_date}.md`` after the brief is
        persisted to Neo4j.  Defaults to False — opt-in per job.
    vault_id
        Target vault (watched-folder ID with ``is_vault=True``).  Ignored
        when ``write_to_vault`` is False; required when it's True.
    vault_folder
        Path prefix under the vault root.  Defaults to ``"_briefs"`` —
        keeps Cerid-authored notes segregated from user content.
    """

    job_type = "brief_generation"

    def __init__(
        self,
        target_date: str,
        *,
        write_to_vault: bool = False,
        vault_id: str | None = None,
        vault_folder: str | None = None,
    ) -> None:
        self._target_date = target_date
        self._write_to_vault = bool(write_to_vault)
        self._vault_id = vault_id
        self._vault_folder = vault_folder or "_briefs"

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

        # --- 2.5 (Task 2.1b) Best-effort claim verification ----------------
        # Off the hot path: a verification failure must never fail brief
        # generation. See _verify_and_persist_claims for the swallow.
        claims = await _verify_and_persist_claims(driver, record, self._target_date)
        if claims:
            record = record.model_copy(
                update={"claim_ids": [c["claim_id"] for c in claims]}
            )

        # --- 3. Persist to Neo4j ------------------------------------------
        await brief_service.store(record, driver)

        # --- 4. (RAG C3.4) Optional vault writeback -----------------------
        # Off the hot path of the brief pipeline — the brief is already
        # persisted in Neo4j, this is purely a side-effect for the user's
        # vault.  Helper swallows all exceptions; we still hop to a
        # thread because vault_write does synchronous filesystem I/O.
        vault_written = False
        if self._write_to_vault and self._vault_id:
            vault_written = await asyncio.to_thread(
                _vault_write_brief,
                target_date=self._target_date,
                vault_id=self._vault_id,
                vault_folder=self._vault_folder,
                record=record,
            )

        await progress_cb(1.0)

        logger.info(
            "brief_generation.done target_date=%s brief_id=%s status=%s vault_written=%s",
            self._target_date,
            record.brief_id,
            record.status,
            vault_written,
        )

        return JobResult(
            job_id="",  # filled in by the worker after dequeue
            actual_tokens_in=_EST_TOKENS_IN,
            actual_tokens_out=_EST_TOKENS_OUT,
            metadata={
                "target_date": self._target_date,
                "brief_id": record.brief_id,
                "status": record.status,
                "vault_written": vault_written,
            },
        )
