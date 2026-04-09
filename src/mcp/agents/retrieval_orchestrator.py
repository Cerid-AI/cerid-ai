# Copyright (c) 2026 Cerid AI. All rights reserved.
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
    context_sources: dict | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Unified retrieval across KB, memories, and external sources.

    In manual mode, this is a pure pass-through to ``agent_query()``.
    In smart/custom_smart modes, memory recall runs in parallel with the
    KB query and results are grouped into a ``source_breakdown``.

    ``context_sources`` is an absolute gate: ``{kb: bool, memory: bool,
    external: bool}``.  ``None`` or missing keys default to ``True``.
    Disabled sources are never queried (saves latency).
    """
    from agents.query_agent import agent_query

    if memory_top_k is None:
        memory_top_k = MEMORY_RECALL_TOP_K
    if memory_min_score is None:
        memory_min_score = MEMORY_RECALL_MIN_SCORE

    # Parse source gates — default all ON
    _cs = context_sources or {}
    _kb_on = _cs.get("kb", True)
    _mem_on = _cs.get("memory", True)
    _ext_on = _cs.get("external", True)

    # --- Manual mode: pass-through to KB pipeline ---
    if rag_mode == "manual":
        if not _kb_on:
            return {
                "context": "", "sources": [], "confidence": 0.0,
                "domains_searched": [], "total_results": 0,
                "token_budget_used": 0, "graph_results": 0, "results": [],
                "strategy": "conversation_only",
                "source_status": {"kb": "disabled"},
            }
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

    source_status: dict[str, str] = {
        "kb": "ok" if _kb_on else "disabled",
        "memory": "ok" if _mem_on else "disabled",
        "external": "ok" if _ext_on else "disabled",
    }
    timings: dict[str, float] = {}

    # Only create tasks for enabled sources
    parallel_start = _time.monotonic()

    kb_task = asyncio.create_task(agent_query(
        query=query,
        domains=domains,
        top_k=top_k,
        use_reranking=use_reranking,
        conversation_messages=conversation_messages,
        chroma_client=chroma_client,
        redis_client=redis_client,
        neo4j_driver=neo4j_driver,
        **kwargs,
    )) if _kb_on else None

    memory_task = asyncio.create_task(_recall_with_timeout(
        query=query,
        chroma_client=chroma_client,
        neo4j_driver=neo4j_driver,
        top_k=memory_top_k,
        min_score=memory_min_score,
        timeout_ms=MEMORY_RECALL_TIMEOUT_MS,
    )) if _mem_on else None

    external_task = asyncio.create_task(_query_external_sources(
        query=query,
        domain=domains[0] if domains else None,
        timeout=5.0,
    )) if _ext_on else None

    # Gather only enabled tasks
    _tasks = [t for t in (kb_task, memory_task, external_task) if t is not None]
    _raw = await asyncio.gather(*_tasks, return_exceptions=True) if _tasks else []
    _raw_iter = iter(_raw)

    kb_result = next(_raw_iter) if kb_task else {"results": [], "context": "", "strategy": "disabled"}
    memory_results = next(_raw_iter) if memory_task else []
    external_results = next(_raw_iter) if external_task else []

    timings["parallel_kb_memory_ms"] = round(
        (_time.monotonic() - parallel_start) * 1000, 1,
    )

    # Handle KB exceptions
    if isinstance(kb_result, (RetrievalError, ValueError, OSError, RuntimeError,
                              AttributeError, TypeError, KeyError)):
        logger.error("KB query failed: %s", kb_result)
        kb_result = {"results": [], "context": "", "strategy": "error"}
        source_status["kb"] = "error"
    elif isinstance(kb_result, BaseException):
        logger.error("KB query failed with unexpected error: %s", kb_result)
        kb_result = {"results": [], "context": "", "strategy": "error"}
        source_status["kb"] = "error"

    # Handle memory exceptions
    if isinstance(memory_results, BaseException):
        logger.warning("Memory recall failed: %s", memory_results)
        memory_results = []
        source_status["memory"] = "error"

    # Handle external source exceptions (fire-and-forget — never block KB)
    if isinstance(external_results, BaseException):
        logger.warning("External source query failed: %s", external_results)
        external_results = []
        source_status["external"] = "error"

    # Separate any legacy external-tagged KB results and merge with real external
    ext_start = _time.monotonic()
    kb_sources = []
    external_sources = []
    for r in kb_result.get("results", []):
        if r.get("source_type") == "external" or r.get("source_url"):
            external_sources.append(r)
        else:
            kb_sources.append(r)

    # Normalize real external results to match frontend ExternalSourceResult shape.
    # External sources use hardcoded confidence (not semantic similarity), so
    # discount them to prevent book-metadata noise from outranking KB results.
    _EXTERNAL_RELEVANCE_DISCOUNT = 0.6
    for raw in external_results:
        raw_confidence = raw.get("confidence", raw.get("relevance", 0.0))
        external_sources.append({
            "content": raw.get("content", ""),
            "relevance": round(raw_confidence * _EXTERNAL_RELEVANCE_DISCOUNT, 3),
            "source_url": raw.get("source_url", ""),
            "source_name": raw.get("source_name", raw.get("title", "")),
            "source_type": "external",
        })

    timings["external_ms"] = round((_time.monotonic() - ext_start) * 1000, 1)
    if not external_sources and source_status["external"] != "disabled":
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


async def _query_external_sources(
    query: str,
    domain: str | None = None,
    timeout: float = 5.0,
) -> list[dict]:
    """Query all enabled external data sources with a hard timeout.

    Returns empty list on any failure — external sources must never block
    the main KB retrieval pipeline.
    """
    try:
        from utils.data_sources import registry

        return await asyncio.wait_for(
            registry.query_all(query, domain=domain, timeout=timeout),
            timeout=timeout + 1.0,  # outer guard above per-source timeout
        )
    except asyncio.TimeoutError:
        logger.warning("External source query timed out after %.1fs", timeout)
        return []
    except Exception as e:  # noqa: BLE001 — graceful degradation
        logger.warning("External source query failed: %s", e)
        return []


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
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
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
