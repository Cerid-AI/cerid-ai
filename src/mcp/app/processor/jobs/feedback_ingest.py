# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""FeedbackIngestJob — queued chat-turn ingestion for the feedback loop.

``POST /ingest/feedback`` used to run the full ingest (chunk → embed →
store) inline; under beta load the endpoint 504'd through the web proxy
(2026-07-12 triage). The heavy tail now runs here on the background
processor. The endpoint keeps the cheap parts (feature gate +
conversation-metrics write) synchronous and acks 202 immediately —
feedback is fire-and-forget in the UI.

Idempotency: ``ingest_content`` dedups by content hash, so a retried or
double-enqueued job converges on the same artifact instead of
duplicating it. The audit-log / cache-invalidation / hallucination-check
tail is best-effort, exactly as it was at request time.

The turn's words are not in the job record (forget spec §6.5): the record
holds ``text_key``, the turn lives in ``app.db.redis.turn_text`` under its
conversation, and is erased once ingested. A job whose text is gone (the
conversation was forgotten, or the text expired) is skipped.

Discovered automatically by ``build_default_registry()``.
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

logger = logging.getLogger("ai-companion.processor.feedback_ingest")

# No direct LLM call at this layer — ingest-time enrichment and the
# hallucination checker carry their own stage= breadcrumbs.
_EST_TOKENS_IN = 0
_EST_TOKENS_OUT = 0
_MODEL = "none"


class FeedbackIngestJob(BaseJob):
    """Ingest one chat turn into the ``conversations`` domain.

    The worker re-instantiates the job as ``FeedbackIngestJob(**record.payload)``;
    the payload carries a reference to the turn, never the turn.
    """

    job_type = "feedback_ingest"

    def __init__(self, text_key: str, model: str = "", conversation_id: str = "") -> None:
        self._text_key = text_key
        self._model = model
        self._conversation_id = conversation_id

    @property
    def priority(self) -> Priority:
        # Fire-and-forget from the chat UI — nobody is blocked on it,
        # but it should not starve behind LOW-tier batch recomputes.
        return Priority.MEDIUM

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
        import config
        from app.db.redis import turn_text
        from app.deps import get_chroma, get_neo4j, get_redis
        from app.services.ingestion import ingest_content
        from core.utils import cache
        from core.utils.time import utcnow

        await progress_cb(0.0)
        from core.forget import registry as forget_registry
        if forget_registry.is_forgotten("conversation", self._conversation_id):
            turn_text.discard(get_redis(), self._text_key)
            return self._skipped("forgotten")
        turn = turn_text.get(get_redis(), self._text_key)
        if not turn:
            return self._skipped("text_gone")
        user_message = turn.get("user_message", "")
        assistant_response = turn.get("assistant_response", "")

        convo_prefix = self._conversation_id[:8] if self._conversation_id else "unknown"
        timestamp = utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"chat_{convo_prefix}_{timestamp}"
        content = (
            f"User: {user_message}\n\n"
            f"Assistant ({self._model}): {assistant_response}"
        )
        metadata = {
            "filename": filename,
            "conversation_id": self._conversation_id,
            "model": self._model,
            "summary": user_message[:200],
        }
        result = await asyncio.to_thread(ingest_content, content, "conversations", metadata)
        await progress_cb(0.6)

        try:
            cache.log_event(
                get_redis(),
                event_type="feedback",
                artifact_id=result.get("artifact_id", ""),
                domain="conversations",
                filename=filename,
                conversation_id=self._conversation_id,
            )
        except Exception as e:
            log_swallowed_error("processor.jobs.feedback_ingest.audit_log", e)

        # Hallucination check — was fire-and-forget at request time, so
        # it stays best-effort here: a checker failure must not fail (and
        # retry) an already-persisted ingest.
        if config.ENABLE_HALLUCINATION_CHECK and result.get("status") == "success":
            try:
                from core.agents.hallucination import check_hallucinations
                await check_hallucinations(
                    response_text=assistant_response,
                    conversation_id=self._conversation_id,
                    chroma_client=get_chroma(),
                    neo4j_driver=get_neo4j(),
                    redis_client=get_redis(),
                    model=self._model,
                )
            except Exception as e:
                log_swallowed_error("processor.jobs.feedback_ingest.hallucination_check", e)

        turn_text.discard(get_redis(), self._text_key)
        await progress_cb(1.0)
        logger.info(
            "feedback_ingest.done convo=%s artifact=%s status=%s",
            convo_prefix, result.get("artifact_id", ""), result.get("status", ""),
        )
        return JobResult(
            job_id="",  # filled in by the worker after dequeue
            actual_tokens_in=0,
            actual_tokens_out=0,
            metadata={
                "artifact_id": result.get("artifact_id", ""),
                "status": result.get("status", ""),
                "filename": filename,
                "conversation_id": self._conversation_id,
            },
        )


    def _skipped(self, reason: str) -> JobResult:
        return JobResult(
            job_id="", actual_tokens_in=0, actual_tokens_out=0,
            metadata={"conversation_id": self._conversation_id, "skipped": reason},
        )


# ── Queue helper (used by the /ingest/feedback endpoint) ─────────────────

def enqueue_feedback_ingest_job(
    *,
    user_message: str,
    assistant_response: str,
    model: str = "",
    conversation_id: str = "",
    redis_client: Any | None = None,
) -> str:
    """Store the turn under its conversation, enqueue a :class:`FeedbackIngestJob`
    that refers to it, and return the job id."""
    from app.db.redis import turn_text  # noqa: PLC0415
    from app.db.redis.processor_queue import enqueue_job  # noqa: PLC0415

    if redis_client is None:
        from app.deps import get_redis  # noqa: PLC0415
        redis_client = get_redis()
    key = turn_text.new_key(conversation_id)
    turn_text.put(redis_client, key, {"user_message": user_message, "assistant_response": assistant_response})
    payload = {"text_key": key, "model": model, "conversation_id": conversation_id}
    try:
        return enqueue_job(FeedbackIngestJob(**payload), payload=payload, redis_client=redis_client)
    except Exception:
        turn_text.discard(redis_client, key)
        raise
