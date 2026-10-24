# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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

import config
from config.constants import (
    EXTERNAL_SOURCE_BROAD_QUERY_TIMEOUT,
    EXTERNAL_SOURCE_QUERY_TIMEOUT,
    EXTERNAL_SOURCE_RELEVANCE_DISCOUNT,
)
from config.settings import (
    MEMORY_RECALL_MIN_SCORE,
    MEMORY_RECALL_TIMEOUT_MS,
    MEMORY_RECALL_TOP_K,
)
from core.agents.crag import crag_wants_external, judge_external
from core.models.external_evidence import ExternalEvidence
from errors import RetrievalError

logger = logging.getLogger("ai-companion.retrieval")

# Plugin hook for custom_smart source weighting (BSL-1.1 plugin injects this).
_custom_rag_fn = None


def set_custom_rag_handler(fn):
    """Called by the custom-rag plugin's register() to inject the implementation."""
    global _custom_rag_fn
    _custom_rag_fn = fn


async def guarded_orchestrated_query(
    *,
    request_context: Any,
    query: str,
    rag_mode: str = "smart",
    domains: list[str] | None = None,
    top_k: int = 10,
    use_reranking: bool = True,
    conversation_messages: list[dict[str, str]] | None = None,
    chroma_client: Any = None,
    redis_client: Any = None,
    neo4j_driver: Any = None,
    graph_store: Any = None,
    context_sources: dict | None = None,
    model: str | None = None,
    exclude_packs: bool = False,
) -> dict[str, Any]:
    """Policy-enforcing wrapper over :func:`orchestrated_query` — the smart-mode
    sibling of :func:`core.agents.guarded_retrieval.guarded_agent_query_full`.

    Private-Mode L2 short-circuits to the KB-bypass envelope BEFORE any retrieval;
    otherwise consumer isolation (``allowed_domains`` / ``strict_domains``) and the
    honored per-request directives (``skip_cache`` / ``metadata_filter`` /
    ``budget_seconds``) come from ``request_context`` — never loose kwargs a caller
    might forget. This lets a transport run smart-mode kb/memory/external
    orchestration WITHOUT dropping the guard the canonical ``/agent/query`` handler
    applies before its smart/manual split, so e.g. a custom agent configured
    ``rag_mode='smart'`` is honored while staying behind the private-mode /
    consumer boundary (E1 CR-095).
    """
    from core.agents.guarded_retrieval import kb_bypassed_envelope

    if request_context.blocks_kb:
        return kb_bypassed_envelope()
    # Memory is the owner's data: a strict scope that leaves out conversations
    # gets none, the wall /agent/query and guarded recall hold memory behind.
    if not request_context.allows_memory:
        context_sources = {**(context_sources or {}), "memory": False}

    return await orchestrated_query(
        query=query,
        rag_mode=rag_mode,
        domains=domains,
        top_k=top_k,
        use_reranking=use_reranking,
        conversation_messages=conversation_messages,
        chroma_client=chroma_client,
        redis_client=redis_client,
        neo4j_driver=neo4j_driver,
        graph_store=graph_store,
        context_sources=context_sources,
        allowed_domains=request_context.allowed_domains_list(),
        strict_domains=request_context.strict_domains,
        domain_record_types=request_context.domain_record_types_dict(),
        skip_cache=request_context.skip_cache,
        metadata_filter=request_context.metadata_filter,
        budget_seconds=request_context.budget_seconds,
        model=model,
        exclude_packs=exclude_packs,
    )


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
    graph_store: Any = None,
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
    from core.agents.query_agent import agent_query  # retrieval-import-allowed: smart-mode sibling of full path

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
            graph_store=graph_store,
            memory_enabled=_mem_on,
            **kwargs,
        )
        return result

    # --- Smart / Custom Smart: parallel KB + memory recall ---
    import time as _time

    # E1 CR-032: no write-only source_status on the public envelope.
    timings: dict[str, float] = {}

    # Only create tasks for enabled sources
    parallel_start = _time.monotonic()

    # E1 R18: when the orchestrator owns memory_task, disable agent_query's
    # internal memory surface so the flatten does not double-count memories.
    kb_task = asyncio.create_task(agent_query(
        query=query,
        domains=domains,
        top_k=top_k,
        use_reranking=use_reranking,
        conversation_messages=conversation_messages,
        chroma_client=chroma_client,
        redis_client=redis_client,
        neo4j_driver=neo4j_driver,
        graph_store=graph_store,
        memory_enabled=False if _mem_on else _mem_on,
        **kwargs,
    )) if _kb_on else None

    memory_task = asyncio.create_task(_recall_with_timeout(
        query=query,
        chroma_client=chroma_client,
        neo4j_driver=neo4j_driver,
        top_k=memory_top_k,
        min_score=memory_min_score,
        timeout_ms=MEMORY_RECALL_TIMEOUT_MS,
        as_of=kwargs.get("as_of"),
        # Verified memories only when the caller names the conversations
        # domain, as on the KB side (ruling D3).
        include_verified="conversations" in (domains or []),
    )) if _mem_on else None

    # Gather only enabled tasks
    _tasks = [t for t in (kb_task, memory_task) if t is not None]
    _raw = await asyncio.gather(*_tasks, return_exceptions=True) if _tasks else []
    _raw_iter = iter(_raw)

    _empty_kb: dict[str, Any] = {"results": [], "context": "", "strategy": "disabled"}
    kb_result: Any = next(_raw_iter) if kb_task else _empty_kb
    memory_results: Any = next(_raw_iter) if memory_task else []

    timings["parallel_kb_memory_ms"] = round(
        (_time.monotonic() - parallel_start) * 1000, 1,
    )

    # gather(return_exceptions=True) hands back a cancellation or interpreter
    # exit as a value; those are not retrieval failures to degrade around.
    for _r in _raw:
        if isinstance(_r, BaseException) and not isinstance(_r, Exception):
            raise _r

    # A failed KB search still lets memory and the web answer, but the envelope
    # says so the way agent_query does for a failed domain: degraded_reason for
    # the user, domains_failed so the result cache does not keep it.
    if isinstance(kb_result, Exception):
        from core.utils.swallowed import log_swallowed_error
        from utils.domain_privacy import owner_domains

        log_swallowed_error("app.agents.retrieval_orchestrator.kb_query", kb_result)
        # Minimal error envelope — flatten rebuild below fills sources/confidence.
        kb_result = {
            "results": [],
            "sources": [],
            "context": "",
            "confidence": 0.0,
            "total_results": 0,
            "strategy": "error",
            "error": str(kb_result),
            "domains_searched": [],
            "domains_failed": list(domains or kwargs.get("allowed_domains") or owner_domains()),
            "retrieval_degraded": True,
            "degraded_reason": "The knowledge-base search failed — results may be missing.",
        }

    # External sources answer to the same CRAG gate as manual mode, decided on
    # the KB result: they ran in parallel and unconditionally here, so smart
    # mode sent every question to the web, greetings and answerable ones
    # included (14/14 answerable, 6/6 greetings on the live probe). With the KB
    # off there is no KB to correct, and the gate fires as before.
    external_results: Any = []
    if _ext_on and crag_wants_external(
        kb_result, query, getattr(config, "RETRIEVAL_QUALITY_THRESHOLD", 0.6),
    ):
        external_results = await _query_external_sources(
            query=query,
            domain=domains[0] if domains else None,
            timeout=EXTERNAL_SOURCE_BROAD_QUERY_TIMEOUT,
        )

    # Handle memory exceptions
    if isinstance(memory_results, BaseException):
        logger.warning("Memory recall failed: %s", memory_results)
        memory_results = []

    # Handle external source exceptions (fire-and-forget — never block KB)
    if isinstance(external_results, BaseException):
        logger.warning("External source query failed: %s", external_results)
        external_results = []

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
    # A source's confidence is a constant, not a match to the question: it is
    # discounted, then the reranker scores the rows the way it scored the KB's.
    fetched = []
    for raw in external_results:
        ev = ExternalEvidence.from_mapping(raw)
        rel = ev.relevance if ev.relevance is not None else 0.0
        fetched.append({
            "content": ev.content,
            "relevance": round(rel * EXTERNAL_SOURCE_RELEVANCE_DISCOUNT, 3),
            "source_url": ev.url,
            "source_name": ev.source_name or ev.title,
            "guid": ev.guid or "",
            "filename": ev.source_name,
            "domain": "external",
            "source_type": "external",
            # RAG Phase 1.1 — best-effort provenance date (None when the
            # external connector result carries no publish/fetch timestamp).
            "created_at": raw.get("created_at") or raw.get("published_at"),
        })
    accepted = await judge_external(query, fetched)
    external_sources.extend(accepted)

    timings["external_ms"] = round((_time.monotonic() - ext_start) * 1000, 1)

    # Format memory results for source_breakdown
    memory_sources = [
        {
            "content": m.get("text", ""),
            "relevance": m.get("adjusted_score", 0.0),
            "memory_type": m.get("memory_type", "empirical"),
            "age_days": m.get("age_days", 0.0),
            "summary": m.get("summary", ""),
            "memory_id": m.get("memory_id", ""),
            "forget_kind": m.get("forget_kind", "artifact"),
            "source_authority": m.get("source_authority", 0.7),
            "base_similarity": m.get("base_similarity", 0.0),
            "access_count": m.get("access_count", 0),
            "source_type": "memory",
            # RAG Phase 1.1 — best-effort provenance date (None when the
            # recalled memory dict carries no creation timestamp).
            "created_at": m.get("created_at"),
        }
        for m in memory_results
    ]

    # Apply custom_smart source config (weights/toggles) via Pro plugin
    if rag_mode == "custom_smart" and source_config and _custom_rag_fn is not None:
        kb_sources, memory_sources, external_sources = _custom_rag_fn(
            kb_sources, memory_sources, external_sources, source_config,
        )

    # top_k caps what is returned, as agent_query_full caps the manual path:
    # the KB result here is agent_query's, uncapped (a gui query with top_k 10
    # came back with 71 rows). Ranked first so memory and web rows compete for
    # the budget instead of queueing behind every KB row.
    if top_k > 0:
        kept = {id(r) for r in sorted(
            [*kb_sources, *memory_sources, *external_sources],
            key=lambda r: float(r.get("relevance") or 0.0), reverse=True,
        )[:top_k]}
        kb_sources = [r for r in kb_sources if id(r) in kept]
        memory_sources = [r for r in memory_sources if id(r) in kept]
        external_sources = [r for r in external_sources if id(r) in kept]

    # Build source_breakdown
    source_breakdown = {
        "kb": kb_sources,
        "memory": memory_sources,
        "external": external_sources,
    }

    # Enrich the base result with orchestrator data
    kb_result["source_breakdown"] = source_breakdown
    kb_result["rerank_rejected"] = int(kb_result.get("rerank_rejected") or 0) + len(fetched) - len(accepted)
    kb_result["rag_mode"] = rag_mode
    kb_result["_timings"] = timings

    # Honor the flatten invariant: the flat results/sources pool must equal
    # flatten(source_breakdown) so the SDK, the retrieval_ndcg metric, and the
    # console see memory + external hits — not just KB (CR-055). The base result
    # carried KB-only values, and the KB-error branch shipped a 3-key dict with
    # no sources/confidence/total_results at all. Rebuilding here fixes both;
    # confidence becomes the mean relevance over the FULL pool (a deliberate
    # semantics change — eval floors are re-baselined alongside).
    flat_pool = [*kb_sources, *memory_sources, *external_sources]
    kb_result["results"] = flat_pool
    kb_result["sources"] = flat_pool
    kb_result["total_results"] = len(flat_pool)
    _relevances = [s.get("relevance", 0.0) for s in flat_pool]
    kb_result["confidence"] = (
        round(min(1.0, max(0.0, sum(_relevances) / len(_relevances))), 4) if _relevances else 0.0
    )

    # Append memory context to the assembled context string
    if memory_sources:
        memory_context = _format_memory_context(memory_sources)
        existing_context = kb_result.get("context", "")
        if existing_context:
            kb_result["context"] = f"{existing_context}\n\n{memory_context}"
        else:
            kb_result["context"] = memory_context

    return kb_result


def _extract_search_terms(query: str) -> str:
    """Extract keyword search terms from a natural-language query.

    External data sources (PubChem, OpenLibrary, DuckDuckGo, etc.) are
    keyword-oriented APIs, not natural-language search engines.  Passing
    a full question like "What is the current population of Tokyo?" produces
    garbage results.  This strips stop-words and question scaffolding,
    returning compact search terms (e.g. "population Tokyo").
    """
    from utils.metadata import _extract_keywords_simple

    keywords = _extract_keywords_simple(query, max_keywords=5)
    return " ".join(keywords) if keywords else query


async def _query_external_sources(
    query: str,
    domain: str | None = None,
    timeout: float = EXTERNAL_SOURCE_QUERY_TIMEOUT,
) -> list[dict]:
    """Query all enabled external data sources with a hard timeout.

    Returns empty list on any failure — external sources must never block
    the main KB retrieval pipeline.
    """
    from core.agents.external_query import external_query_for

    # A personal question goes out only as a public-fact rewrite, or not at all.
    outgoing = await external_query_for(query)
    if outgoing is None:
        return []
    try:
        from app.data_sources import registry
        from utils.metadata import _extract_keywords_simple

        # Extract keywords — external APIs need search terms, not full questions
        keywords = _extract_keywords_simple(outgoing, max_keywords=5)
        search_terms = " ".join(keywords) if keywords else outgoing
        logger.debug("External source search terms: %r → %r", outgoing, search_terms)

        return await asyncio.wait_for(
            registry.query_all(
                search_terms,
                domain=domain,
                timeout=timeout,
                raw_query=outgoing,
                keywords=keywords,
            ),
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
    as_of: str | None = None,
    include_verified: bool = False,
) -> list[dict]:
    """Run memory recall with a hard timeout, returning empty on failure."""
    try:
        from app.agents.memory import recall_memories

        return await asyncio.wait_for(
            recall_memories(
                query=query,
                chroma_client=chroma_client,
                neo4j_driver=neo4j_driver,
                top_k=top_k,
                min_score=min_score,
                as_of=as_of,
                include_verified=include_verified,
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
