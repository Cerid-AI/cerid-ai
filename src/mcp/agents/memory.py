# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

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
) -> dict[str, Any]:
    """Bridge wrapper that injects ``ingest_content`` when no ingest_fn is provided."""
    if ingest_fn is None:
        from services.ingestion import ingest_content
        ingest_fn = ingest_content

    return await _core_extract_and_store_memories(
        response_text=response_text,
        conversation_id=conversation_id,
        model=model,
        chroma_client=chroma_client,
        neo4j_driver=neo4j_driver,
        redis_client=redis_client,
        ingest_fn=ingest_fn,
    )
