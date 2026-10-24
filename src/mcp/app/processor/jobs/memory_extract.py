# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Memory extraction as a background processor job.

Ported from the retired ``app.queue`` RQ system (its ``memory_extract_task``
worker entrypoint). The SDK's ``POST /sdk/v1/memory/extract`` async path
enqueues this job onto the canonical processor ``RedisJobQueue``; callers poll
``GET /sdk/v1/memory/extract/jobs/{job_id}`` for the result envelope.

Why off the request path: extraction fans out to N memories ×
(consolidation + conflict-resolution) LLM calls plus Neo4j/Chroma writes. The
synchronous path was sized against cloud latency; on a local daemon it is
~15× slower and running it inline blows the interactive p99<10s SLO (measured
>120s once per-memory consolidation and conflict-resolution are included). See
:func:`core.agents.memory.extract_and_store_memories`.

Neither the caller's text nor the result is in the job record (forget spec
§6.5): the record lives 14 days and holds ``text_key``. The text, and then the
``SDKMemoryExtractResponse`` envelope (it carries each memory's summary), live
in ``app.db.redis.turn_text`` under the conversation, where the status
endpoint reads the result and a conversation forget erases both.

Payload schema
--------------
  {"text_key": str, "conversation_id": str, "model": str}
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal

from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobResult, ProgressCallback
from core.processor.priority import Priority
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.memory_extract")

# Extraction + consolidation + conflict-resolution fan out to several LLM
# calls, so the budget sits above the single-claim entity-extraction job.
# Zero marginal USD on the local Ollama path (the worker's model policy may
# still route to an API model under a cost cap, like every requires_llm job).
_EST_TOKENS_IN = 2_000
_EST_TOKENS_OUT = 800
_MODEL = "ollama/local"


class MemoryExtractJob(BaseJob):
    """Extract facts/decisions/preferences from conversation text and store them."""

    job_type = "memory_extract"

    def __init__(self, text_key: str, conversation_id: str, model: str = "") -> None:
        self._text_key = text_key
        self._conversation_id = conversation_id
        self._model = model

    @property
    def priority(self) -> Priority:
        # Interactive-adjacent: a caller is typically polling the status
        # endpoint for the result. Above bulk LOW maintenance jobs.
        return Priority.MEDIUM

    def estimate_cost(self) -> CostEstimate:
        """LLM cost estimate — zero USD on the local Ollama path."""
        return CostEstimate(
            estimated_tokens_in=_EST_TOKENS_IN,
            estimated_tokens_out=_EST_TOKENS_OUT,
            model=_MODEL,
            estimated_usd=Decimal("0.00"),
            confidence="low",
        )

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        """Run the extract → consolidate → store pipeline for one conversation.

        Errors are logged then re-raised so the worker records the failure and
        schedules a retry; retry logic lives in the worker, not here.
        """
        # Lazy imports keep worker boot light and resolve the store singletons
        # inside the worker process (the request-side DI chain isn't available
        # here). ``app.agents.memory`` injects ingest_fn + observation_date.
        from app.agents.memory import extract_and_store_memories
        from app.db.redis import turn_text
        from app.deps import get_chroma, get_neo4j, get_redis

        await progress_cb(0.0)
        response_text = turn_text.get(get_redis(), self._text_key).get("response_text")
        if response_text is None:
            # Forgotten or expired: there is nothing left to extract from.
            return JobResult(
                job_id="", actual_tokens_in=0, actual_tokens_out=0,
                metadata={"conversation_id": self._conversation_id, "skipped": "text_gone"},
            )
        logger.info(
            "memory_extract.start conversation=%s text_len=%d", self._conversation_id, len(response_text),
        )

        try:
            envelope = await extract_and_store_memories(
                response_text=response_text,
                conversation_id=self._conversation_id,
                model=self._model,
                chroma_client=get_chroma(),
                neo4j_driver=get_neo4j(),
                redis_client=get_redis(),
            )
        except Exception as exc:
            log_swallowed_error(
                "processor.memory_extract",
                exc,
                context={"conversation_id": self._conversation_id},
            )
            raise

        await progress_cb(1.0)
        logger.info(
            "memory_extract.done conversation=%s extracted=%s stored=%s skipped=%s",
            self._conversation_id,
            envelope.get("memories_extracted"),
            envelope.get("memories_stored"),
            envelope.get("skipped_duplicates"),
        )

        # A forget that landed while this ran erased the key; writing the
        # result then would bring the summaries back.
        from core.forget import registry as forget_registry

        stored = not forget_registry.is_forgotten("conversation", self._conversation_id) and (
            turn_text.replace_if_present(
                get_redis(), self._text_key, {"result": json.dumps(envelope)}, drop=("response_text",),
            )
        )
        return JobResult(
            job_id="",  # filled in by the worker after dequeue
            actual_tokens_in=_EST_TOKENS_IN,
            actual_tokens_out=_EST_TOKENS_OUT,
            metadata={"conversation_id": self._conversation_id, "result_stored": stored},
        )


def stored_result(text_key: str) -> dict | None:
    """The result envelope of a finished job, or None when it is gone."""
    from app.db.redis import turn_text
    from app.deps import get_redis

    raw = turn_text.get(get_redis(), text_key).get("result")
    return json.loads(raw) if raw else None


def enqueue_payload(response_text: str, conversation_id: str, model: str = "") -> dict[str, str]:
    """Store the caller's text under its conversation and return the job payload
    that refers to it."""
    from app.db.redis import turn_text
    from app.deps import get_redis

    key = turn_text.new_key(conversation_id)
    turn_text.put(get_redis(), key, {"response_text": response_text})
    return {"text_key": key, "conversation_id": conversation_id, "model": model}
