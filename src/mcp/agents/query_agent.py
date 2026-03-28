# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Multi-domain knowledge base search with LLM reranking."""

import asyncio
import logging
import time
from contextlib import contextmanager
from typing import Any

import numpy as np

import config
from config import DOMAINS
from utils.cache import log_event

# ---------------------------------------------------------------------------
# Re-export bridges -- backward compatibility for existing importers
# ---------------------------------------------------------------------------
from agents.decomposer import (  # noqa: F401
    _enrich_query,
    _format_chroma_result,
    _get_adjacent_domains,
    graph_expand_results,
    lightweight_kb_query,
    multi_domain_query,
)
from agents.assembler import (  # noqa: F401
    _apply_quality_and_summaries,
    _enrich_summaries,
    _rerank_cross_encoder,
    _rerank_llm,
    apply_context_alignment_boost,
    apply_metadata_boost,
    apply_quality_boost,
    assemble_context,
    deduplicate_results,
    rerank_results,
)

logger = logging.getLogger("ai-companion.query_agent")


# ---------------------------------------------------------------------------
# Step timer for latency tracing
# ---------------------------------------------------------------------------

class StepTimer:
    """Lightweight latency tracker for pipeline steps.

    Zero overhead when disabled — the ``step()`` context manager becomes a no-op.
    """

    __slots__ = ("_enabled", "_timings", "_t0")

    def __init__(self, enabled: bool = False):
        self._enabled = enabled
        self._timings: dict[str, float] = {}
        self._t0 = time.monotonic() if enabled else 0.0

    @contextmanager
    def step(self, name: str):
        if not self._enabled:
            yield
            return
        start = time.monotonic()
        yield
        self._timings[name] = round(time.monotonic() - start, 4)

    def result(self) -> dict[str, float]:
        if not self._enabled:
            return {}
        self._timings["total"] = round(time.monotonic() - self._t0, 4)
        return dict(self._timings)


# ---------------------------------------------------------------------------
# Main query agent function
# ---------------------------------------------------------------------------

async def agent_query(
    query: str,
    domains: list[str] | None = None,
    top_k: int = 10,
    use_reranking: bool = True,
    conversation_messages: list[dict[str, str]] | None = None,
    chroma_client: Any | None = None,
    redis_client: Any | None = None,
    neo4j_driver: Any | None = None,
    debug_timing: bool = False,
    allowed_domains: list[str] | None = None,
    strict_domains: bool = False,
    model: str | None = None,
) -> dict[str, Any]:
    """Execute multi-domain query with reranking, graph expansion, and context assembly."""
    timer = StepTimer(enabled=debug_timing)

    # ---------------------------------------------------------------------------
    # Degradation tier gate — adjust behavior based on system health
    # ---------------------------------------------------------------------------
    _tier = None
    try:
        from config.features import ENABLE_DEGRADATION_TIERS
        if ENABLE_DEGRADATION_TIERS:
            from utils.degradation import DegradationTier, degradation
            _tier = degradation.current_tier()
            if _tier == DegradationTier.OFFLINE:
                logger.info("Degradation tier: OFFLINE — returning static error")
                return {"context": "", "sources": [], "confidence": 0.0,
                        "domains_searched": domains if domains else DOMAINS,
                        "total_results": 0, "token_budget_used": 0,
                        "graph_results": 0, "results": [],
                        "degradation_tier": "offline"}
    except Exception as exc:
        logger.debug("Degradation check skipped: %s", exc)

    from config.features import (
        ENABLE_ADAPTIVE_RETRIEVAL,
        ENABLE_INTELLIGENT_ASSEMBLY,
        ENABLE_LATE_INTERACTION,
        ENABLE_MMR_DIVERSITY,
        ENABLE_QUERY_DECOMPOSITION,
        ENABLE_SEMANTIC_CACHE,
    )

    # Degradation: CACHED tier — only serve from semantic cache, skip retrieval
    # DIRECT tier — skip retrieval, go straight to LLM (use_kb effectively False)
    _degradation_skip_retrieval = False
    _degradation_cache_only = False
    try:
        if _tier == DegradationTier.CACHED:
            _degradation_cache_only = True
            _degradation_skip_retrieval = True
            logger.info("Degradation tier: CACHED — semantic cache only")
        elif _tier == DegradationTier.DIRECT:
            _degradation_skip_retrieval = True
            logger.info("Degradation tier: DIRECT — skipping retrieval")
    except Exception:
        pass

    # Semantic cache early-return — check before any retrieval work
    _query_embedding: np.ndarray | None = None
    with timer.step("semantic_cache_lookup"):
        if ENABLE_SEMANTIC_CACHE and redis_client:
            try:
                from utils.embeddings import get_embedding_function
                from utils.semantic_cache import cache_lookup
                _ef = get_embedding_function()
                if _ef is not None:
                    _query_embedding = np.asarray(_ef([query])[0])
                    cached = cache_lookup(_query_embedding, redis_client)
                    if cached is not None:
                        cached["semantic_cache_hit"] = True
                        return cached
            except Exception as e:
                logger.debug("Semantic cache lookup skipped: %s", e)

    # Degradation: CACHED tier — if semantic cache missed, return empty
    if _degradation_cache_only:
        return {"context": "", "sources": [], "confidence": 0.0,
                "domains_searched": domains if domains else DOMAINS,
                "total_results": 0, "token_budget_used": 0,
                "graph_results": 0, "results": [],
                "degradation_tier": "cached", "retrieval_skipped": True,
                "retrieval_reason": "degradation_cached_miss"}

    # Degradation: DIRECT tier — skip all retrieval, return empty KB context
    if _degradation_skip_retrieval:
        return {"context": "", "sources": [], "confidence": 0.0,
                "domains_searched": domains if domains else DOMAINS,
                "total_results": 0, "token_budget_used": 0,
                "graph_results": 0, "results": [],
                "degradation_tier": "direct", "retrieval_skipped": True,
                "retrieval_reason": "degradation_direct"}

    search_query = query
    if conversation_messages:
        search_query = _enrich_query(
            query, conversation_messages, max_context_messages=config.QUERY_CONTEXT_MESSAGES,
        )
        if search_query != query:
            logger.info(f"Enriched query: {query!r} → {search_query!r}")

    # Step 0: Adaptive retrieval gate — may short-circuit or reduce top_k
    effective_top_k = top_k
    if ENABLE_ADAPTIVE_RETRIEVAL:
        from utils.retrieval_gate import classify_retrieval_need
        decision = classify_retrieval_need(query)
        if decision.action == "skip":
            logger.info("Retrieval gate: skip (%s)", decision.reason)
            return {
                "context": "",
                "sources": [],
                "confidence": 0.0,
                "domains_searched": domains if domains else DOMAINS,
                "total_results": 0,
                "token_budget_used": 0,
                "graph_results": 0,
                "results": [],
                "retrieval_skipped": True,
                "retrieval_reason": decision.reason,
            }
        if decision.action == "light":
            effective_top_k = decision.top_k
            logger.info("Retrieval gate: light (top_k=%d, %s)", effective_top_k, decision.reason)

    # When querying from a chat flow (conversation_messages provided) and no
    # explicit domain filter was requested, exclude the "conversations" domain.
    # Feedback-ingested conversation turns would otherwise dominate results,
    # creating circular noise (same pattern as hallucination.py:87-89).
    effective_domains = domains
    if effective_domains is None and conversation_messages:
        effective_domains = [d for d in config.DOMAINS if d != "conversations"]

    # Consumer domain isolation: restrict to allowed domains if configured
    if allowed_domains is not None:
        if effective_domains is not None:
            effective_domains = [d for d in effective_domains if d in allowed_domains]
        else:
            effective_domains = list(allowed_domains)
        if not effective_domains:
            logger.info("Consumer domain filter removed all requested domains")
            return {
                "context": "",
                "sources": [],
                "confidence": 0.0,
                "domains_searched": [],
                "total_results": 0,
                "token_budget_used": 0,
                "graph_results": 0,
                "results": [],
                "retrieval_skipped": True,
                "retrieval_reason": "consumer_domain_restricted",
            }

    # Step 0.5: Query decomposition — may split into parallel sub-queries
    _skip_normal_retrieval = False
    _retrieval_cache_hit = False
    with timer.step("vector_search"):
        # Retrieval cache: check before hitting ChromaDB
        if _query_embedding is not None:
            try:
                from utils.retrieval_cache import retrieval_cache
                _cached_results = retrieval_cache.get(
                    _query_embedding.tolist(), effective_top_k,
                )
                if _cached_results is not None:
                    results = _cached_results
                    _skip_normal_retrieval = True
                    _retrieval_cache_hit = True
                    logger.debug("Retrieval cache hit (top_k=%d)", effective_top_k)
                else:
                    logger.debug("Retrieval cache miss")
            except Exception as exc:
                logger.debug("Retrieval cache lookup failed: %s", exc)

        if not _skip_normal_retrieval and ENABLE_QUERY_DECOMPOSITION:
            from utils.query_decomposer import decompose_query, needs_decomposition, parallel_retrieve
            if needs_decomposition(search_query):
                sub_queries = await decompose_query(search_query)
                if len(sub_queries) > 1:
                    logger.info("Decomposed query into %d sub-queries: %s", len(sub_queries), sub_queries)

                    async def _retrieve_sub(sq: str) -> list[dict[str, Any]]:
                        return await multi_domain_query(
                            query=sq, domains=effective_domains,
                            top_k=effective_top_k, chroma_client=chroma_client,
                        )

                    results = await parallel_retrieve(sub_queries, _retrieve_sub)
                    _skip_normal_retrieval = True

        if not _skip_normal_retrieval:
            results = await multi_domain_query(
                query=search_query,
                domains=effective_domains,
                top_k=effective_top_k,
                chroma_client=chroma_client,
            )

        # Retrieval cache: store after successful ChromaDB retrieval
        if not _retrieval_cache_hit and _query_embedding is not None and results:
            try:
                from utils.retrieval_cache import retrieval_cache
                retrieval_cache.set(
                    _query_embedding.tolist(), effective_top_k, results,
                )
            except Exception as exc:
                logger.debug("Retrieval cache store failed: %s", exc)

    # Search adjacent domains at reduced weight when specific domains are requested.
    # Skipped when strict_domains=True (consumer isolation — no cross-domain bleed).
    if not strict_domains and domains and set(domains) != set(DOMAINS):
        adjacent = _get_adjacent_domains(domains)
        if adjacent:
            cross_results = await multi_domain_query(
                query=search_query,
                domains=list(adjacent.keys()),
                top_k=max(3, top_k // 2),
                chroma_client=chroma_client,
            )
            for r in cross_results:
                r["relevance"] = round(
                    r["relevance"] * adjacent.get(r["domain"], config.CROSS_DOMAIN_DEFAULT_AFFINITY),
                    4,
                )
                r["cross_domain"] = True
            results.extend(cross_results)

    # HyDE fallback — if top score is below threshold, generate hypothetical doc and re-search
    if results and not _retrieval_cache_hit:
        try:
            from utils.hyde import generate_hypothetical_document, reciprocal_rank_fusion, should_trigger_hyde
            _top_score = results[0].get("relevance", 0) if results else 0
            if should_trigger_hyde(_top_score):
                _hyde_domain = (effective_domains[0] if effective_domains and len(effective_domains) == 1
                                else None)
                _hypothetical = await generate_hypothetical_document(search_query, _hyde_domain)
                if _hypothetical:
                    _hyde_results = await multi_domain_query(
                        query=_hypothetical,
                        domains=effective_domains,
                        top_k=effective_top_k,
                        chroma_client=chroma_client,
                    )
                    if _hyde_results:
                        results = reciprocal_rank_fusion(results, _hyde_results)
                        logger.debug("HyDE fallback activated, merged %d results", len(results))
        except Exception as exc:
            logger.debug("HyDE fallback skipped: %s", exc)

    results = deduplicate_results(results)

    with timer.step("graph_expansion"):
        graph_count_before = len(results)
        # Early-exit: skip graph expansion when vector search already returned
        # enough high-confidence results (saves a Neo4j round-trip).
        _high_conf = [r for r in results if r.get("relevance", 0) > 0.8]
        if len(_high_conf) >= effective_top_k:
            logger.debug(
                "Skipping graph expansion: %d/%d results above 0.8 confidence",
                len(_high_conf), effective_top_k,
            )
        else:
            results = await graph_expand_results(
                results=results,
                query=query,
                chroma_client=chroma_client,
                neo4j_driver=neo4j_driver,
            )
        graph_results_added = len(results) - graph_count_before

    from utils.temporal import is_within_window, parse_temporal_intent, recency_score
    temporal_days = parse_temporal_intent(query)

    if temporal_days is not None:
        results = [
            r for r in results
            if is_within_window(
                r.get("ingested_at", ""),
                temporal_days,
            )
        ]

    for r in results:
        ingested = r.get("ingested_at", "")
        if ingested:
            boost = recency_score(ingested) * config.TEMPORAL_RECENCY_WEIGHT
            r["relevance"] = round(r["relevance"] + boost, 4)

    # Step 4.5: Metadata boost — surface tag/sub_category-aligned results before reranking
    results = apply_metadata_boost(results, query)

    # Step 4.6: Context alignment boost — reward results matching conversation context
    results = apply_context_alignment_boost(results, conversation_messages)

    # Step 5: Reranking (includes both direct and graph-sourced results)
    with timer.step("reranking"):
        results = await rerank_results(
            results=results,
            query=query,
            use_reranking=use_reranking,
        )

    # Step 5.1: Late interaction refinement — ColBERT-style MaxSim on top candidates
    with timer.step("late_interaction"):
        if ENABLE_LATE_INTERACTION and results:
            try:
                from utils.embeddings import get_embedding_function
                from utils.late_interaction import late_interaction_rerank
                _ef = get_embedding_function()
                if _ef is not None:
                    results = late_interaction_rerank(
                        results=results, query=query, embed_fn=_ef,
                    )
            except Exception as e:
                logger.warning("Late interaction scoring failed: %s", e)

    # Step 5.5: Quality boost + summary enrichment — single Neo4j round-trip
    with timer.step("quality_boost"):
        results = await asyncio.to_thread(_apply_quality_and_summaries, results, neo4j_driver)
        results = sorted(results, key=lambda x: x["relevance"], reverse=True)

    # Step 5.6: MMR diversity reordering — reduce redundancy in top results
    with timer.step("mmr_diversity"):
        if ENABLE_MMR_DIVERSITY and len(results) > 1:
            try:
                from utils.diversity import mmr_reorder
                results = mmr_reorder(results=results, query=query)
            except Exception as e:
                logger.warning("MMR diversity reordering failed: %s", e)

    # Step 5.7: Filter low-relevance results below minimum threshold
    results = [r for r in results if r["relevance"] >= config.QUALITY_MIN_RELEVANCE_THRESHOLD]

    # Step 6: Assemble context
    with timer.step("context_assembly"):
        # Model-aware context budget — large-context models get more KB context
        try:
            ctx_budget = config.get_context_budget_for_model(model)
        except (AttributeError, TypeError):
            ctx_budget = getattr(config, "QUERY_CONTEXT_MAX_CHARS", 14_000)
        if not isinstance(ctx_budget, (int, float)):
            ctx_budget = 14_000
        if ENABLE_INTELLIGENT_ASSEMBLY and results:
            try:
                from utils.context_assembler import intelligent_assemble
                context, sources, coverage_meta = intelligent_assemble(
                    results=results, query=query, max_chars=ctx_budget,
                )
                char_count = len(context)
            except Exception as e:
                logger.warning("Intelligent assembly failed, falling back: %s", e)
                context, sources, char_count = assemble_context(results, max_chars=ctx_budget)
        else:
            context, sources, char_count = assemble_context(results, max_chars=ctx_budget)

    # Step 7: Calculate confidence (average relevance of included sources)
    confidence = 0.0
    if sources:
        confidence = sum(s["relevance"] for s in sources) / len(sources)

    # Step 8: Log query (optional)
    if redis_client:
        try:
            log_event(
                redis_client,
                event_type="query",
                artifact_id="",
                domain=",".join(domains) if domains else "all",
                filename="",
                extra={
                    "query": query,
                    "results": len(results),
                    "graph_results": graph_results_added,
                },
            )
        except Exception as e:
            logger.warning(f"Failed to log query: {e}")

    result_dict: dict[str, Any] = {
        "context": context,
        "sources": sources,
        "confidence": round(confidence, 4),
        "domains_searched": domains if domains else DOMAINS,
        "total_results": len(results),
        "token_budget_used": char_count,
        "graph_results": graph_results_added,
        "results": results,
    }

    timings = timer.result()
    if timings:
        result_dict["_timings"] = timings

    # Semantic cache store — persist result for similar future queries
    if ENABLE_SEMANTIC_CACHE and redis_client and _query_embedding is not None:
        try:
            from utils.semantic_cache import cache_store
            cache_store(query, _query_embedding, result_dict, redis_client)
        except Exception as e:
            logger.debug("Semantic cache store failed: %s", e)

    return result_dict
