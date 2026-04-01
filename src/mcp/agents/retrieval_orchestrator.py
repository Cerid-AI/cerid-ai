# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unified Retrieval Orchestrator — combines KB, memory, and external sources.

Wraps ``agent_query()`` without modifying the existing 22-step RAG pipeline.
Three modes:

- **manual**: pass-through to ``agent_query()`` (existing behavior)
- **smart**: parallel KB + memory recall, external source separation
- **custom_smart** (Pro): smart + user-configurable source weights/toggles
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config.settings import (
    MEMORY_RECALL_MIN_SCORE,
    MEMORY_RECALL_TIMEOUT_MS,
    MEMORY_RECALL_TOP_K,
)
from errors import RetrievalError

logger = logging.getLogger("ai-companion.retrieval")

# Plugin hook for custom_smart source weighting (BSL-1.1 plugin injects this).
_custom_rag_fn = None


def set_custom_rag_handler(fn):
    """Called by the custom-rag plugin's register() to inject the implementation."""
    global _custom_rag_fn
    _custom_rag_fn = fn


async def orchestrated_query(
    query: str,
    rag_mode: str = "manual",
    domains: list[str] | None = None,
    top_k: int = 10,
    use_reranking: bool = True,
    conversation_messages: list[dict[str, str]] | None = None,
    chroma_client: Any = None,
    redis_client: Any = None,
    neo4j_driver: Any = None,
    memory_top_k: int | None = None,
    memory_min_score: float | None = None,
    source_config: dict | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Unified retrieval across KB, memories, and external sources.

    In manual mode, this is a pure pass-through to ``agent_query()``.
    In smart/custom_smart modes, memory recall runs in parallel with the
    KB query and results are grouped into a ``source_breakdown``.
    """
    from agents.query_agent import agent_query

    if memory_top_k is None:
        memory_top_k = MEMORY_RECALL_TOP_K
    if memory_min_score is None:
        memory_min_score = MEMORY_RECALL_MIN_SCORE

    # --- Manual mode: pure pass-through ---
    if rag_mode == "manual":
        result = await agent_query(
            query=query,
            domains=domains,
            top_k=top_k,
            use_reranking=use_reranking,
            conversation_messages=conversation_messages,
            chroma_client=chroma_client,
            redis_client=redis_client,
            neo4j_driver=neo4j_driver,
            **kwargs,
        )
        return result

    # --- Smart / Custom Smart: parallel KB + memory recall ---
    import time as _time

    source_status: dict[str, str] = {"kb": "ok", "memory": "ok", "external": "ok"}
    timings: dict[str, float] = {}

    # KB query with timing and error handling
    kb_start = _time.monotonic()
    try:
        kb_result = await agent_query(
            query=query,
            domains=domains,
            top_k=top_k,
            use_reranking=use_reranking,
            conversation_messages=conversation_messages,
            chroma_client=chroma_client,
            redis_client=redis_client,
            neo4j_driver=neo4j_driver,
            **kwargs,
        )
    except (RetrievalError, ValueError, OSError, RuntimeError) as kb_exc:
        logger.error("KB query failed: %s", kb_exc)
        kb_result = {"results": [], "context": "", "strategy": "error"}
        source_status["kb"] = "error"
    timings["kb_ms"] = round((_time.monotonic() - kb_start) * 1000, 1)

    # Memory recall with timing (already has timeout/error handling internally)
    mem_start = _time.monotonic()
    memory_results = await _recall_with_timeout(
        query=query,
        chroma_client=chroma_client,
        neo4j_driver=neo4j_driver,
        top_k=memory_top_k,
        min_score=memory_min_score,
        timeout_ms=MEMORY_RECALL_TIMEOUT_MS,
    )
    timings["memory_ms"] = round((_time.monotonic() - mem_start) * 1000, 1)
    if not memory_results and source_status["memory"] == "ok":
        # Check if it was a timeout vs just no results
        if timings["memory_ms"] >= MEMORY_RECALL_TIMEOUT_MS * 0.95:
            source_status["memory"] = "timeout"

    # Separate external results from KB results
    ext_start = _time.monotonic()
    kb_sources = []
    external_sources = []
    for r in kb_result.get("results", []):
        if r.get("source_type") == "external" or r.get("source_url"):
            external_sources.append(r)
        else:
            kb_sources.append(r)
    timings["external_ms"] = round((_time.monotonic() - ext_start) * 1000, 1)
    if not external_sources:
        source_status["external"] = "no_results"

    # Format memory results for source_breakdown
    memory_sources = [
        {
            "content": m.get("text", ""),
            "relevance": m.get("adjusted_score", 0.0),
            "memory_type": m.get("memory_type", "empirical"),
            "age_days": m.get("age_days", 0.0),
            "summary": m.get("summary", ""),
            "memory_id": m.get("memory_id", ""),
            "source_authority": m.get("source_authority", 0.7),
            "base_similarity": m.get("base_similarity", 0.0),
            "access_count": m.get("access_count", 0),
            "source_type": "memory",
        }
        for m in memory_results
    ]

    # Apply custom_smart source config (weights/toggles) via Pro plugin
    if rag_mode == "custom_smart" and source_config and _custom_rag_fn is not None:
        kb_sources, memory_sources, external_sources = _custom_rag_fn(
            kb_sources, memory_sources, external_sources, source_config,
        )

    # Build source_breakdown
    source_breakdown = {
        "kb": kb_sources,
        "memory": memory_sources,
        "external": external_sources,
    }

    # Enrich the base result with orchestrator data
    kb_result["source_breakdown"] = source_breakdown
    kb_result["rag_mode"] = rag_mode
    kb_result["source_status"] = source_status
    kb_result["_timings"] = timings

    # Append memory context to the assembled context string
    if memory_sources:
        memory_context = _format_memory_context(memory_sources)
        existing_context = kb_result.get("context", "")
        if existing_context:
            kb_result["context"] = f"{existing_context}\n\n{memory_context}"
        else:
            kb_result["context"] = memory_context

    return kb_result


async def _recall_with_timeout(
    query: str,
    chroma_client: Any,
    neo4j_driver: Any,
    top_k: int,
    min_score: float,
    timeout_ms: int,
) -> list[dict]:
    """Run memory recall with a hard timeout, returning empty on failure."""
    try:
        from agents.memory import recall_memories

        return await asyncio.wait_for(
            recall_memories(
                query=query,
                chroma_client=chroma_client,
                neo4j_driver=neo4j_driver,
                top_k=top_k,
                min_score=min_score,
            ),
            timeout=timeout_ms / 1000.0,
        )
    except asyncio.TimeoutError:
        logger.warning("Memory recall timed out after %dms", timeout_ms)
        return []
    except (RetrievalError, ValueError, OSError, RuntimeError) as e:
        logger.warning("Memory recall failed: %s", e)
        return []


def _format_memory_context(memory_sources: list[dict]) -> str:
    """Format memory results as a context block for the LLM."""
    lines = ["[Memory Context]"]
    for m in memory_sources:
        summary = m.get("summary") or m.get("content", "")[:80]
        mem_type = m.get("memory_type", "")
        score = m.get("relevance", 0.0)
        lines.append(f"- [{mem_type}] {summary} (score: {score:.2f})")
    return "\n".join(lines)


    # NOTE: _apply_source_config() has been extracted to the custom-rag plugin
    # (plugins/custom-rag/plugin.py, BSL-1.1). The plugin's register() injects
    # the weighting function via set_custom_rag_handler().
