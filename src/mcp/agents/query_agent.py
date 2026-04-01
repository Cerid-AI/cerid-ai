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
from agents.assembler import (
    _apply_quality_and_summaries,
    apply_context_alignment_boost,
    apply_metadata_boost,
    assemble_context,
    deduplicate_results,
    rerank_results,
)
from agents.decomposer import (
    _enrich_query,
    _get_adjacent_domains,
    graph_expand_results,
    multi_domain_query,
)
from config import DOMAINS
from errors import RetrievalError
from utils.cache import log_event

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
    except (RetrievalError, ValueError, OSError, RuntimeError) as exc:
        logger.debug("Degradation check skipped: %s", exc)

    from config.features import (
        ENABLE_ADAPTIVE_RETRIEVAL,
        ENABLE_GRAPH_RAG,
        ENABLE_INTELLIGENT_ASSEMBLY,
        ENABLE_LATE_INTERACTION,
        ENABLE_MMR_DIVERSITY,
        ENABLE_PARENT_CHILD_RETRIEVAL,
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
    except (RetrievalError, ValueError, OSError, RuntimeError) as e:
        logger.warning("Degradation tier check failed: %s", e)

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
            except (RetrievalError, ValueError, OSError, RuntimeError) as e:
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

    # Smart Auto-RAG: classify intent and adjust retrieval
    try:
        from utils.query_classifier import classify_query_intent, get_rag_config
        _query_intent = classify_query_intent(query)
        _rag_config = get_rag_config(_query_intent)

        # Get RAG mode from settings (smart/always/manual)
        import config as _cfg
        _rag_mode = getattr(_cfg, "RAG_MODE", "smart")
        if _rag_mode == "always":
            _rag_config = get_rag_config("factual")  # Force full RAG
        elif _rag_mode == "manual":
            _rag_config = get_rag_config("conversational")  # Skip auto RAG

        if _rag_config["top_k"] == 0:
            # Conversational — skip retrieval entirely
            return {"context": "", "sources": [], "confidence": 0.0,
                    "domains_searched": domains if domains else DOMAINS,
                    "total_results": 0, "token_budget_used": 0,
                    "graph_results": 0, "results": [],
                    "intent": _query_intent, "rag_skipped": True}
    except (RetrievalError, ValueError, OSError, RuntimeError) as exc:
        logger.debug("Smart Auto-RAG classification skipped: %s", exc)
        _query_intent = "factual"
        _rag_config = {"inject": True, "top_k": 10, "decompose": True, "rerank": True}

    from utils.agent_events import emit_agent_event
    emit_agent_event("query", f"On it \u2014 searching {len(domains or DOMAINS)} domains for you...")

    search_query = query
    if conversation_messages:
        search_query = _enrich_query(
            query, conversation_messages, max_context_messages=config.QUERY_CONTEXT_MESSAGES,
        )
        if search_query != query:
            logger.info(f"Enriched query: {query!r} → {search_query!r}")

    # Step 0: Adaptive retrieval gate — may short-circuit or reduce top_k
    effective_top_k = min(top_k, _rag_config["top_k"]) if _rag_config["top_k"] > 0 else top_k
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
            except (RetrievalError, ValueError, OSError, RuntimeError) as exc:
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
            except (RetrievalError, ValueError, OSError, RuntimeError) as exc:
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
        except (RetrievalError, ValueError, OSError, RuntimeError) as exc:
            logger.debug("HyDE fallback skipped: %s", exc)

    results = deduplicate_results(results)

    # Step 3.5: Parent-child retrieval — swap child chunks for their richer parents
    with timer.step("parent_child_lookup"):
        if ENABLE_PARENT_CHILD_RETRIEVAL and chroma_client and results:
            try:
                results = _resolve_parent_chunks(results, chroma_client)
            except (RetrievalError, ValueError, OSError, RuntimeError) as exc:
                logger.warning("Parent-child lookup failed: %s", exc)

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

    # Step 3.7: Graph RAG — entity-aware retrieval via knowledge graph
    _graph_rag_count = 0
    with timer.step("graph_rag"):
        if ENABLE_GRAPH_RAG and neo4j_driver:
            try:
                _graph_rag_results = await _graph_rag_retrieve(
                    query=query,
                    neo4j_driver=neo4j_driver,
                    chroma_client=chroma_client,
                    existing_results=results,
                )
                _graph_rag_count = len(_graph_rag_results)
                if _graph_rag_results:
                    results.extend(_graph_rag_results)
                    results = deduplicate_results(results)
                    logger.debug("Graph RAG added %d results", _graph_rag_count)
            except (RetrievalError, ValueError, OSError, RuntimeError) as exc:
                logger.warning("Graph RAG retrieval failed: %s", exc)

    # Enrich with external data sources for factual/analytical queries
    try:
        from utils.data_sources import registry as data_source_registry
        if _query_intent in ("factual", "analytical") and data_source_registry.has_enabled_sources():
            _ext_results = await data_source_registry.query_all(query)
            if _ext_results:
                # Add external results with lower relevance weight
                for er in _ext_results[:3]:  # max 3 external results
                    results.append({
                        "content": f"[{er['source_name']}] {er['content']}",
                        "relevance": er.get("confidence", 0.7) * 0.8,  # slightly lower than KB
                        "artifact_id": f"external:{er['source_name'].lower()}",
                        "filename": er.get("title", er["source_name"]),
                        "domain": "external",
                        "chunk_index": 0,
                        "source_url": er.get("source_url", ""),
                    })
                logger.debug("Added %d external data source results", len(_ext_results[:3]))
    except (RetrievalError, ValueError, OSError, RuntimeError) as exc:
        logger.debug("External data source enrichment skipped: %s", exc)

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
            except (RetrievalError, ValueError, OSError, RuntimeError) as e:
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
            except (RetrievalError, ValueError, OSError, RuntimeError) as e:
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
            except (RetrievalError, ValueError, OSError, RuntimeError) as e:
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
        except (RetrievalError, ValueError, OSError, RuntimeError) as e:
            logger.warning(f"Failed to log query: {e}")

    emit_agent_event(
        "query",
        f"Found {len(results)} results across {len(sources)} sources (confidence {confidence:.2f})",
        level="success",
    )

    result_dict: dict[str, Any] = {
        "context": context,
        "sources": sources,
        "confidence": round(confidence, 4),
        "domains_searched": domains if domains else DOMAINS,
        "total_results": len(results),
        "token_budget_used": char_count,
        "graph_results": graph_results_added,
        "graph_rag_results": _graph_rag_count,
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
        except (RetrievalError, ValueError, OSError, RuntimeError) as e:
            logger.debug("Semantic cache store failed: %s", e)

    return result_dict


# ---------------------------------------------------------------------------
# Parent-child retrieval helpers
# ---------------------------------------------------------------------------

def _resolve_parent_chunks(
    results: list[dict[str, Any]],
    chroma_client: Any,
) -> list[dict[str, Any]]:
    """Swap child chunks for their parent chunks to provide richer context.

    When a child chunk matches during retrieval, fetch the parent chunk
    from ChromaDB (using the ``parent_chunk_id`` stored in metadata) and
    return the parent's broader text instead. Deduplicates so that
    multiple children from the same parent yield only one parent entry.
    """
    if not results:
        return results

    # Collect child results that have a parent_chunk_id
    child_results: list[dict[str, Any]] = []
    non_child_results: list[dict[str, Any]] = []

    for r in results:
        chunk_level = r.get("chunk_level", "")
        parent_id = r.get("parent_chunk_id", "")
        if chunk_level == "child" and parent_id:
            child_results.append(r)
        else:
            non_child_results.append(r)

    if not child_results:
        return results

    # Group children by parent_chunk_id, keeping best relevance per parent
    parent_map: dict[str, dict[str, Any]] = {}
    for child in child_results:
        pid = child["parent_chunk_id"]
        if pid not in parent_map or child.get("relevance", 0) > parent_map[pid].get("relevance", 0):
            parent_map[pid] = child

    # Fetch parent chunks from ChromaDB
    resolved_parents: list[dict[str, Any]] = []

    # Determine which collections to search — group by domain
    domain_parents: dict[str, list[str]] = {}
    for pid, child_data in parent_map.items():
        domain = child_data.get("domain", "general")
        domain_parents.setdefault(domain, []).append(pid)

    for domain, pids in domain_parents.items():
        try:
            coll_name = config.collection_name(domain)
            collection = chroma_client.get_or_create_collection(name=coll_name)
            fetched = collection.get(ids=pids, include=["documents", "metadatas"])
            if fetched and fetched.get("ids"):
                for i, fid in enumerate(fetched["ids"]):
                    doc = fetched["documents"][i] if fetched.get("documents") else ""
                    # Use the best child's relevance score for the parent
                    child_data = parent_map.get(fid, {})
                    resolved_parents.append({
                        **child_data,
                        "content": doc,
                        "chunk_level": "parent",
                        "parent_chunk_id": fid,
                        "parent_resolved": True,
                    })
        except (RetrievalError, ValueError, OSError, RuntimeError) as exc:
            logger.debug("Parent chunk fetch failed for domain %s: %s", domain, exc)
            # Fall back to original child results for this domain
            for pid in pids:
                if pid in parent_map:
                    resolved_parents.append(parent_map[pid])

    # Combine: non-child results + resolved parents
    seen_ids: set[str] = set()
    combined: list[dict[str, Any]] = []
    for r in non_child_results:
        aid = r.get("artifact_id", "")
        cid = r.get("chunk_id", "") or r.get("parent_chunk_id", "")
        key = f"{aid}:{cid}"
        if key not in seen_ids:
            seen_ids.add(key)
            combined.append(r)

    for r in resolved_parents:
        aid = r.get("artifact_id", "")
        cid = r.get("parent_chunk_id", "")
        key = f"{aid}:{cid}"
        if key not in seen_ids:
            seen_ids.add(key)
            combined.append(r)

    logger.debug(
        "Parent-child resolution: %d children → %d parents (from %d total results)",
        len(child_results), len(resolved_parents), len(results),
    )
    return combined


# ---------------------------------------------------------------------------
# Graph RAG retrieval helper
# ---------------------------------------------------------------------------

async def _graph_rag_retrieve(
    query: str,
    neo4j_driver: Any,
    chroma_client: Any,
    existing_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Run entity-aware graph retrieval and return blended results.

    1. Extract entities from the query.
    2. Traverse Neo4j graph to find related artifacts.
    3. Fetch chunk content from ChromaDB for matched artifacts.
    4. Score with blended graph_score.
    5. Exclude artifacts already in existing_results.
    """
    from db.neo4j.graph_rag import graph_retrieve
    from utils.entity_extraction import extract_entities

    entities = extract_entities(query)
    if not entities:
        return []

    graph_results = await graph_retrieve(neo4j_driver, entities)
    if not graph_results:
        return []

    # Filter out artifacts already present in existing results
    existing_aids = {r.get("artifact_id", "") for r in existing_results}
    novel_graph = [g for g in graph_results if g["artifact_id"] not in existing_aids]
    if not novel_graph:
        return []

    # Fetch representative chunks from ChromaDB for the graph-sourced artifacts
    graph_weight = getattr(config, "GRAPH_RAG_WEIGHT", 0.3)
    enriched: list[dict[str, Any]] = []

    # Group by domain for efficient ChromaDB lookups
    domain_aids: dict[str, list[dict[str, Any]]] = {}
    for gr in novel_graph:
        domain_aids.setdefault(gr["domain"], []).append(gr)

    for domain, items in domain_aids.items():
        if not domain:
            continue
        try:
            coll_name = config.collection_name(domain)
            collection = chroma_client.get_or_create_collection(name=coll_name)
            for item in items:
                # Fetch first chunk of the artifact for content
                chunk_id_prefix = f"{item['artifact_id']}_chunk_0"
                try:
                    fetched = collection.get(
                        ids=[chunk_id_prefix],
                        include=["documents", "metadatas"],
                    )
                    if fetched and fetched.get("ids"):
                        doc = fetched["documents"][0] if fetched.get("documents") else ""
                        meta = fetched["metadatas"][0] if fetched.get("metadatas") else {}
                        enriched.append({
                            "content": doc,
                            "relevance": round(item["graph_score"] * graph_weight, 4),
                            "artifact_id": item["artifact_id"],
                            "filename": item["filename"],
                            "domain": domain,
                            "chunk_index": 0,
                            "graph_sourced": True,
                            "graph_score": item["graph_score"],
                            "hop_distance": item.get("hop_distance", 0),
                            **{k: v for k, v in meta.items()
                               if k in ("ingested_at", "sub_category", "tags_json")},
                        })
                except (RetrievalError, ValueError, OSError, RuntimeError):
                    # Chunk not found — use summary as fallback content
                    if item.get("summary"):
                        enriched.append({
                            "content": item["summary"],
                            "relevance": round(item["graph_score"] * graph_weight * 0.8, 4),
                            "artifact_id": item["artifact_id"],
                            "filename": item["filename"],
                            "domain": domain,
                            "chunk_index": 0,
                            "graph_sourced": True,
                            "graph_score": item["graph_score"],
                        })
        except (RetrievalError, ValueError, OSError, RuntimeError) as exc:
            logger.debug("Graph RAG ChromaDB lookup failed for domain %s: %s", domain, exc)

    return enriched
