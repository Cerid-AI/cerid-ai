# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""SessionSummaryJob — once-per-conversation semantic session summary.

Bi-temporal memory plan Phase E (E1). The Graphiti episode -> semantic
layering: the per-response memories a conversation already produced are the
"episodes"; this job consolidates them into one semantic summary artifact that
sits above them (the multi-session recall lever).

Everything here is DARK behind ``ENABLE_SESSION_SUMMARIZATION`` (config/features
Phase E gate) — the job no-ops when the flag is off. The scheduler scan
(``app.scheduler._run_session_summaries``) finds idle conversations lacking a
summary and enqueues one job per conversation (``enqueue_session_summary_job``,
dedup via ``enqueue_job_if_absent``).

Why ingest through the same path per-response extraction uses
(``services.ingestion.ingest_content`` into the ``conversations`` domain):
the summary then rides the SAME downstream machinery for free — bi-temporal
valid-interval stamping, the post-commit entity-extraction enqueue (Phase K1.1,
which carries Phase-C ``:Fact`` derivation gated by ``ENABLE_FACT_WRITES``), and
recall. The only additions over a plain memory are ``memory_scope`` +
``conversation_id`` metadata (so the summary is distinguishable from the
per-response memories it consolidates) and the ``EXTRACTED_FROM`` provenance
edge, mirroring the per-response path.

Idempotent: a conversation already carrying a ``memory_scope=session_summary``
artifact is skipped (queryable marker), and duplicate per-conversation enqueues
collapse via ``enqueue_job_if_absent``.

Discovered automatically by ``build_default_registry()``.

Payload schema
--------------
  {"conversation_id": str, "tenant_id": str}
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
from core.utils.time import utcnow, utcnow_iso

logger = logging.getLogger("ai-companion.processor.session_summary")

_CONVERSATIONS_DOMAIN = "conversations"
_SESSION_SUMMARY_SCOPE = "session_summary"

# One local-LLM call (session summarization); zero marginal USD on the Ollama
# path, same as entity_extraction.
_EST_TOKENS_IN = 2_500
_EST_TOKENS_OUT = 512
_MODEL = "ollama/local"


class SessionSummaryJob(BaseJob):
    """Summarize one conversation's session into a consolidated memory artifact.

    Parameters
    ----------
    conversation_id
        The :Conversation id to summarize (its per-response memories are the
        summary input).
    tenant_id
        Tenant identifier (log correlation; single-tenant stacks pass any
        stable string).
    """

    job_type = "session_summary"

    def __init__(self, conversation_id: str, tenant_id: str = "default") -> None:
        self._conversation_id = conversation_id
        self._tenant_id = tenant_id

    @property
    def priority(self) -> Priority:
        # Background consolidation — never contends with user-triggered work.
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        """Zero USD — session summarization runs on the local Ollama path."""
        return CostEstimate(
            estimated_tokens_in=_EST_TOKENS_IN,
            estimated_tokens_out=_EST_TOKENS_OUT,
            model=_MODEL,
            estimated_usd=Decimal("0.00"),
            confidence="medium",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        """Execute the session-summary pipeline for the configured conversation.

        Progress checkpoints
        --------------------
        0.0  — job started
        0.2  — idempotency check passed (no existing summary)
        0.5  — turns fetched
        0.8  — summary generated (one LLM call)
        1.0  — summary ingested + linked

        A clean skip (flag off, no memories, already summarized, empty summary)
        returns a ``JobResult`` with a ``skipped`` reason — never an error, so
        the worker does not retry. Only unexpected store failures re-raise for
        the worker's retry path (mirrors ``EntityExtractionJob``).
        """
        await progress_cb(0.0)

        # DARK gate — imported inside run() so tests can monkeypatch the flag
        # and so a flip takes effect without a process restart (mirrors the
        # ENABLE_FACT_WRITES check in entity_extraction._derive_and_write_facts).
        from config.features import ENABLE_SESSION_SUMMARIZATION

        if not ENABLE_SESSION_SUMMARIZATION:
            return self._skip("feature_disabled")

        logger.info(
            "session_summary.start conversation=%s tenant=%s",
            self._conversation_id,
            self._tenant_id,
        )
        try:
            stats = await self._run_pipeline(progress_cb)
        except Exception as exc:
            log_swallowed_error(
                "processor.session_summary",
                exc,
                context={
                    "conversation_id": self._conversation_id,
                    "tenant_id": self._tenant_id,
                },
            )
            raise

        return JobResult(
            job_id="",  # filled in by the worker after dequeue
            actual_tokens_in=_EST_TOKENS_IN,
            actual_tokens_out=_EST_TOKENS_OUT,
            metadata={
                "conversation_id": self._conversation_id,
                "tenant_id": self._tenant_id,
                **stats,
            },
        )

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    def _skip(self, reason: str) -> JobResult:
        return JobResult(
            job_id="",
            actual_tokens_in=0,
            actual_tokens_out=0,
            metadata={
                "conversation_id": self._conversation_id,
                "tenant_id": self._tenant_id,
                "skipped": reason,
            },
        )

    async def _run_pipeline(self, progress_cb: ProgressCallback) -> dict[str, Any]:
        from app.deps import get_chroma, get_neo4j

        driver = get_neo4j()
        chroma_client = get_chroma()
        cid = self._conversation_id

        # 1. Idempotency — a session-summary artifact already exists?
        if await asyncio.to_thread(_summary_exists, driver, cid):
            return {"skipped": "already_summarized"}
        await progress_cb(0.2)

        # 2. Fetch the session's turns (the per-response memories in Chroma).
        turns = await asyncio.to_thread(_fetch_session_turns, chroma_client, cid)
        if not turns:
            return {"skipped": "no_memories"}
        turns_text = _assemble_turns_text(turns)
        session_date = _resolve_session_date(turns)
        await progress_cb(0.5)

        # 3. One LLM call — consolidate.
        from core.agents.session_summary import default_llm_caller, summarize_session

        summary = await summarize_session(
            turns_text=turns_text,
            conversation_id=cid,
            session_date=session_date,
            llm_caller=default_llm_caller,
        )
        if summary is None:
            return {"skipped": "summary_empty", "memory_count": len(turns)}
        await progress_cb(0.8)

        # 4. Ingest through the same path per-response extraction uses.
        artifact_id = await asyncio.to_thread(
            _ingest_summary, cid, session_date, summary
        )
        if not artifact_id:
            return {"skipped": "ingest_failed", "memory_count": len(turns)}

        # 5. Link provenance + stamp the queryable idempotency marker.
        await asyncio.to_thread(_link_and_mark, driver, artifact_id, cid)
        await progress_cb(1.0)

        logger.info(
            "session_summary.done conversation=%s artifact=%s memories=%d",
            cid,
            artifact_id,
            len(turns),
        )
        return {
            "summarized": True,
            "artifact_id": artifact_id,
            "memory_count": len(turns),
        }


# ---------------------------------------------------------------------------
# Store helpers (module-level so the tests can drive them in isolation and the
# job body stays readable). Synchronous — the job wraps them in to_thread.
# ---------------------------------------------------------------------------


def _summary_exists(driver: Any, conversation_id: str) -> bool:
    """True when the conversation already carries a session-summary artifact."""
    if driver is None:
        return False
    with driver.session() as session:
        row = session.run(
            "MATCH (a:Artifact)-[:EXTRACTED_FROM]->(c:Conversation {id: $cid}) "
            "WHERE a.memory_scope = $scope "
            "RETURN a.id AS id LIMIT 1",
            cid=conversation_id,
            scope=_SESSION_SUMMARY_SCOPE,
        ).single()
    return row is not None


def _fetch_session_turns(chroma_client: Any, conversation_id: str) -> list[dict[str, Any]]:
    """Fetch the conversation's per-response memory chunks from Chroma.

    Production persists NO raw transcript (the /sdk memory-extract endpoint is
    per-response + stateless; the :Conversation node is id-only), so the durable
    per-conversation content is the extracted memories, which already carry
    ``conversation_id`` in their Chroma metadata. Returns one dict per chunk
    (``content``, ``created_at``, ``valid_from``), summary chunks excluded.
    """
    if chroma_client is None:
        return []
    import config

    try:
        coll = chroma_client.get_or_create_collection(
            name=config.collection_name(_CONVERSATIONS_DOMAIN)
        )
        res = coll.get(
            where={"conversation_id": {"$eq": conversation_id}},
            include=["documents", "metadatas"],
        )
    except Exception:  # noqa: BLE001 — collection-missing / empty is a valid no-memories skip
        return []

    docs = list(res.get("documents", []) or [])
    metas = list(res.get("metadatas", []) or [])
    turns: list[dict[str, Any]] = []
    for i, doc in enumerate(docs):
        meta = metas[i] if i < len(metas) else {}
        if str(meta.get("memory_scope", "")) == _SESSION_SUMMARY_SCOPE:
            continue  # never fold a prior summary back into itself
        text = (doc or "").strip()
        if not text:
            continue
        turns.append(
            {
                "content": text,
                "created_at": str(meta.get("created_at", "")),
                "valid_from": str(meta.get("valid_from", "")),
            }
        )
    return turns


def _assemble_turns_text(turns: list[dict[str, Any]]) -> str:
    """Concatenate the session's turns chronologically (by created_at)."""
    ordered = sorted(turns, key=lambda t: t.get("created_at", ""))
    return "\n\n---\n\n".join(t["content"] for t in ordered if t.get("content"))


def _resolve_session_date(turns: list[dict[str, Any]]) -> str:
    """The session's date (ISO YYYY-MM-DD): the latest valid_from among the
    turns (world-time — the day the conversation was about), falling back to
    the latest created_at (system-time). Empty when neither is known."""
    valid_froms = [t["valid_from"][:10] for t in turns if t.get("valid_from")]
    if valid_froms:
        return max(valid_froms)
    created = [t["created_at"][:10] for t in turns if t.get("created_at")]
    return max(created) if created else ""


def _ingest_summary(
    conversation_id: str, session_date: str, summary: dict[str, Any]
) -> str:
    """Ingest the summary as a conversations-domain memory artifact.

    Builds the SAME bi-temporal metadata the per-response path stamps (reusing
    ``resolve_valid_from`` / ``OPEN_INTERVAL`` from ``fact_derivation`` — the
    stamping is not reimplemented, only the two distinguishing fields
    ``memory_scope`` + ``conversation_id`` are added). Ingesting via
    ``ingest_content`` gives the summary its Chroma chunk, Neo4j :Artifact node,
    and the post-commit entity-extraction enqueue (-> Phase-C fact derivation)
    for free. Returns the artifact id, or "" when ingest did not create one.
    """
    from app.services.ingestion import ingest_content
    from core.agents.fact_derivation import OPEN_INTERVAL, resolve_valid_from

    now_iso = utcnow_iso()
    convo_prefix = conversation_id[:8] if conversation_id else "unknown"
    filename = f"session_summary_{convo_prefix}_{utcnow().strftime('%Y%m%d_%H%M%S')}"

    metadata: dict[str, Any] = {
        "filename": filename,
        "conversation_id": conversation_id,
        "memory_type": summary["memory_type"],
        "memory_scope": _SESSION_SUMMARY_SCOPE,
        "summary": summary["summary"],
        "created_at": now_iso,
        "valid_from": resolve_valid_from(
            event_date=summary.get("event_date"),
            observation_date=session_date,
        ),
        "valid_to": OPEN_INTERVAL,
        "decay_anchor": now_iso,
        "access_count": "0",
    }
    if summary.get("event_date"):
        metadata["event_date"] = summary["event_date"]

    result = ingest_content(
        summary["content"], _CONVERSATIONS_DOMAIN, metadata=metadata
    )
    if result.get("status") not in ("success", "duplicate"):
        return ""
    return str(result.get("artifact_id", ""))


def _link_and_mark(driver: Any, artifact_id: str, conversation_id: str) -> None:
    """Write the EXTRACTED_FROM provenance edge (mirrors the per-response path)
    and stamp ``memory_scope`` on the Neo4j node so the summary is a queryable
    idempotency marker (``create_artifact`` writes only its fixed column set, so
    the scope lands here)."""
    if driver is None or not artifact_id:
        return
    with driver.session() as session:
        session.run(
            "MATCH (a:Artifact {id: $aid}) "
            "MERGE (c:Conversation {id: $cid}) "
            "MERGE (a)-[:EXTRACTED_FROM]->(c) "
            "SET a.memory_scope = $scope",
            aid=artifact_id,
            cid=conversation_id,
            scope=_SESSION_SUMMARY_SCOPE,
        )


# ── Queue helpers (mirror app.processor.jobs.digest_run) ──────────────────

def active_session_summary_jobs(redis_client: Any | None = None) -> list[str]:
    """Return job ids of queued or running ``session_summary`` jobs.

    Reads the processor queue's own key layout (pending priority lists +
    running set) so no parallel bookkeeping can drift from the queue.
    """
    from app.db.redis.processor_queue import (  # noqa: PLC0415
        _RUNNING_KEY,
        _job_key,
        _queue_key,
    )
    from core.processor.priority import priority_order  # noqa: PLC0415

    if redis_client is None:
        from app.deps import get_redis  # noqa: PLC0415
        redis_client = get_redis()

    def _s(v: Any) -> str:
        return v.decode() if isinstance(v, bytes) else str(v)

    job_ids: list[str] = []
    for priority in priority_order():
        job_ids.extend(_s(j) for j in redis_client.lrange(_queue_key(priority), 0, -1))
    job_ids.extend(_s(j) for j in redis_client.smembers(_RUNNING_KEY))

    active: list[str] = []
    for job_id in job_ids:
        job_type = redis_client.hget(_job_key(job_id), "job_type")
        if job_type is not None and _s(job_type) == SessionSummaryJob.job_type:
            active.append(job_id)
    return active


def enqueue_session_summary_job(
    conversation_id: str,
    tenant_id: str = "default",
    redis_client: Any | None = None,
) -> str | None:
    """Enqueue a :class:`SessionSummaryJob` for one conversation, collapsing a
    duplicate that is still pending/running (``enqueue_job_if_absent``).

    Returns the new job id, or ``None`` when collapsed onto an existing job.
    """
    from app.db.redis.processor_queue import enqueue_job_if_absent  # noqa: PLC0415

    payload = {"conversation_id": conversation_id, "tenant_id": tenant_id}
    return enqueue_job_if_absent(
        SessionSummaryJob(**payload), payload=payload, redis_client=redis_client
    )
