# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Re-export bridge — see core/agents/memory.py for implementation.

Wraps ``extract_and_store_memories`` to inject ``ingest_fn`` from
``services.ingestion`` so callers at the app layer don't need to
pass it explicitly.
"""
from __future__ import annotations

from typing import Any

from core.agents.memory import (  # noqa: F401
    MEMORY_TYPES,
    MIN_RESPONSE_LENGTH,
    archive_old_memories,
    calculate_memory_score,
    call_internal_llm,
    config,
    detect_memory_conflict,
    extract_memories,
    recall_memories,
    resolve_memory_conflict,
)
from core.agents.memory import (
    extract_and_store_memories as _core_extract_and_store_memories,
)


async def extract_and_store_memories(
    response_text: str,
    conversation_id: str,
    model: str = "",
    chroma_client: Any = None,
    neo4j_driver: Any = None,
    redis_client: Any = None,
    ingest_fn: Any = None,
    observation_date: str | None = None,
) -> dict[str, Any]:
    """Bridge wrapper that injects ``ingest_content`` when no ingest_fn is provided.

    Defaults ``observation_date`` to today for the live ingestion path: every
    app-layer caller (chat tool, /memory/extract routes, queue task) reaches
    extraction for a conversation that is happening *now*, so "now" is the
    correct anchor. Without it the LLM cannot resolve relative dates ("last
    Monday") to an absolute ``event_date`` and the memory lands date-blind —
    invisible to temporal retrieval/arithmetic. Pass an explicit date only when
    ingesting historical content (the eval calls core ``extract_memories``
    directly with the session date, so it is unaffected by this default).
    """
    if ingest_fn is None:
        from app.services.ingestion import ingest_content
        ingest_fn = ingest_content

    if observation_date is None:
        from core.utils.time import utcnow_iso
        observation_date = utcnow_iso()[:10]

    return await _core_extract_and_store_memories(
        response_text=response_text,
        conversation_id=conversation_id,
        model=model,
        chroma_client=chroma_client,
        neo4j_driver=neo4j_driver,
        redis_client=redis_client,
        ingest_fn=ingest_fn,
        observation_date=observation_date,
    )
