# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Multi-domain knowledge base search with LLM reranking."""

import asyncio
import json
import logging
import re
from collections import defaultdict
from typing import Any

import httpx

import config
from config import BIFROST_URL, DOMAINS
from deps import get_chroma
from middleware.request_id import tracing_headers
from utils.cache import log_event
from utils.circuit_breaker import CircuitOpenError, get_breaker
from utils.llm_parsing import parse_llm_json

logger = logging.getLogger("ai-companion.query_agent")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_chroma_result(
    content: str,
    relevance: float,
    chunk_id: str,
    domain: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Build a standardized result dict from a ChromaDB chunk."""
    return {
        "content": content,
        "relevance": round(relevance, 4),
        "artifact_id": metadata.get("artifact_id", ""),
        "filename": metadata.get("filename", ""),
        "domain": domain,
        "chunk_index": metadata.get("chunk_index", 0),
        "collection": config.collection_name(domain),
        "chunk_id": chunk_id,
        "ingested_at": metadata.get("ingested_at", ""),
        "sub_category": metadata.get("sub_category", ""),
        "tags_json": metadata.get("tags_json", "[]"),
        "keywords": metadata.get("keywords", "[]"),
    }


# ---------------------------------------------------------------------------
# Cross-domain affinity
# ---------------------------------------------------------------------------

def _get_adjacent_domains(requested: list[str]) -> dict[str, float]:
    """Return non-requested domains with their max affinity score."""
    requested_set = set(requested)
    adjacent: dict[str, float] = {}
    for req in requested:
        explicit = config.DOMAIN_AFFINITY.get(req, {})
        for other in DOMAINS:
            if other in requested_set:
                continue
            weight = explicit.get(other, config.CROSS_DOMAIN_DEFAULT_AFFINITY)
            adjacent[other] = max(adjacent.get(other, 0.0), weight)
    return adjacent


# ---------------------------------------------------------------------------
# Conversation-aware query enrichment
# ---------------------------------------------------------------------------

# Common English stopwords to filter from context terms
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "just", "because", "but", "and", "or", "if", "while", "about", "up",
    "that", "this", "these", "those", "am", "what", "which", "who", "whom",
    "its", "it", "he", "she", "they", "them", "his", "her", "my", "your",
    "our", "their", "me", "him", "us", "i", "you", "we", "also", "like",
    "get", "got", "make", "made", "know", "think", "want", "see", "look",
    "find", "give", "tell", "say", "said", "going", "come", "take",
})

_WORD_RE = re.compile(r"[a-zA-Z0-9_]+(?:[-'][a-zA-Z0-9_]+)*")


def _enrich_query(
    query: str,
    conversation_messages: list[dict[str, str]],
    max_context_messages: int = 5,
    max_terms: int = 10,
) -> str:
    """Enrich a query with recency-weighted terms from recent conversation messages.

    More recent messages contribute more terms to the enriched query, improving
    retrieval relevance for the current conversational context. Term slots are
    allocated via exponential decay (newest message gets ~half the budget).

    Returns the original query if no useful context terms are found.
    """
    if not conversation_messages:
        return query

    # Collect text from recent user messages only (skip system/assistant)
    user_texts: list[str] = []
    for msg in conversation_messages[-max_context_messages:]:
        if msg.get("role") == "user":
            user_texts.append(msg.get("content", ""))

    if not user_texts:
        return query

    # Allocate term slots per message using exponential decay (most recent = most slots)
    n = len(user_texts)
    if n == 1:
        slots = [max_terms]
    else:
        raw_weights = [0.5 ** i for i in range(n)]
        total_weight = sum(raw_weights)
        float_slots = [w / total_weight * max_terms for w in raw_weights]
        slots = [max(1, round(s)) for s in float_slots]
        # Adjust to hit exact total
        diff = max_terms - sum(slots)
        if diff > 0:
            slots[0] += diff
        elif diff < 0:
            for i in range(n - 1, -1, -1):
                if slots[i] > 1:
                    remove = min(slots[i] - 1, -diff)
                    slots[i] -= remove
                    diff += remove
                    if diff == 0:
                        break

    # Extract terms per message, respecting per-message slot allocation
    query_terms = {w.lower() for w in _WORD_RE.findall(query)}
    context_terms: list[str] = []
    seen: set = set()

    for idx, text in enumerate(reversed(user_texts)):  # Most recent first
        msg_limit = slots[idx] if idx < len(slots) else 1
        msg_count = 0
        words = _WORD_RE.findall(text)
        for word in words:
            lower = word.lower()
            if (
                len(lower) > 2
                and lower not in _STOPWORDS
                and lower not in query_terms
                and lower not in seen
            ):
                seen.add(lower)
                context_terms.append(lower)
                msg_count += 1
                if msg_count >= msg_limit:
                    break

    if not context_terms:
        return query

    return f"{query} {' '.join(context_terms)}"


# ---------------------------------------------------------------------------
# Multi-domain retrieval
# ---------------------------------------------------------------------------

async def multi_domain_query(
    query: str,
    domains: list[str] | None = None,
    top_k: int = 10,
    chroma_client: Any | None = None
) -> list[dict[str, Any]]:
    """Query multiple ChromaDB collections in parallel and aggregate results."""
    if domains is None:
        domains = DOMAINS

    invalid_domains = [d for d in domains if d not in DOMAINS]
    if invalid_domains:
        raise ValueError(f"Invalid domains: {invalid_domains}. Valid: {DOMAINS}")

    if chroma_client is None:
        chroma_client = get_chroma()

    async def query_domain(domain: str) -> list[dict[str, Any]]:
        """Query a single domain collection (vector + BM25 hybrid)."""
        try:
            collection = chroma_client.get_collection(name=config.collection_name(domain))

            results = collection.query(
                query_texts=[query],
                n_results=top_k,
                include=["documents", "metadatas", "distances"]
            )

            formatted = []
            seen_ids: set = set()
            if results["ids"] and results["ids"][0]:
                for i, chunk_id in enumerate(results["ids"][0]):
                    distance = results["distances"][0][i] if results["distances"] else 1.0
                    relevance = max(0.0, min(1.0, 1.0 - distance))
                    metadata = results["metadatas"][0][i] if results["metadatas"] else {}

                    formatted.append(_format_chroma_result(
                        content=results["documents"][0][i],
                        relevance=relevance,
                        chunk_id=chunk_id,
                        domain=domain,
                        metadata=metadata,
                    ))
                    seen_ids.add(chunk_id)

            from utils import bm25 as bm25_mod
            if bm25_mod.is_available():
                bm25_hits = bm25_mod.search_bm25(domain, query, top_k=top_k)
                if bm25_hits:
                    bm25_map = dict(bm25_hits)

                    for entry in formatted:
                        kw_score = bm25_map.pop(entry["chunk_id"], 0.0)
                        vector_score = entry["relevance"]
                        entry["relevance"] = round(
                            config.HYBRID_VECTOR_WEIGHT * vector_score
                            + config.HYBRID_KEYWORD_WEIGHT * kw_score,
                            4,
                        )

                    if bm25_map:
                        try:
                            bm25_only_ids = list(bm25_map.keys())
                            fetched = collection.get(
                                ids=bm25_only_ids,
                                include=["documents", "metadatas"],
                            )
                            for j, cid in enumerate(fetched["ids"]):
                                if cid in seen_ids:
                                    continue
                                meta = fetched["metadatas"][j] if fetched["metadatas"] else {}
                                formatted.append(_format_chroma_result(
                                    content=fetched["documents"][j],
                                    relevance=config.HYBRID_KEYWORD_WEIGHT * bm25_map[cid],
                                    chunk_id=cid,
                                    domain=domain,
                                    metadata=meta,
                                ))
                                seen_ids.add(cid)
                        except Exception as e:
                            logger.debug(f"BM25-only fetch failed for {domain}: {e}")

            return formatted

        except Exception as e:
            logger.warning(f"Error querying domain {domain}: {e}")
            return []

    tasks = [query_domain(domain) for domain in domains]
    domain_results = await asyncio.gather(*tasks)

    all_results = [r for results in domain_results for r in results]

    return all_results


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate chunks, keeping highest relevance per (artifact_id, chunk_index)."""
    groups = defaultdict(list)
    for result in results:
        key = (result["artifact_id"], result["chunk_index"])
        groups[key].append(result)

    deduplicated = []
    for group in groups.values():
        best = max(group, key=lambda x: x["relevance"])
        deduplicated.append(best)

    return deduplicated


# ---------------------------------------------------------------------------
# Graph-enhanced retrieval
# ---------------------------------------------------------------------------

async def graph_expand_results(
    results: list[dict[str, Any]],
    query: str,
    chroma_client: Any | None = None,
    neo4j_driver: Any | None = None,
) -> list[dict[str, Any]]:
    """Expand results by traversing the knowledge graph for related artifacts."""
    if neo4j_driver is None or not results:
        return results

    from utils.graph import find_related_artifacts

    initial_ids = list({r["artifact_id"] for r in results if r.get("artifact_id")})
    if not initial_ids:
        return results

    try:
        related = find_related_artifacts(
            neo4j_driver,
            artifact_ids=initial_ids,
            depth=config.GRAPH_TRAVERSAL_DEPTH,
            max_results=config.GRAPH_MAX_RELATED,
        )
    except Exception as e:
        logger.warning(f"Graph traversal failed (continuing without): {e}")
        return results

    if not related:
        return results

    if chroma_client is None:
        chroma_client = get_chroma()

    existing_ids = {r.get("chunk_id") for r in results}
    graph_results: list[dict[str, Any]] = []

    for rel_artifact in related:
        try:
            chunk_ids_json = rel_artifact.get("chunk_ids", "[]")
            chunk_ids = json.loads(chunk_ids_json) if chunk_ids_json else []
            if not chunk_ids:
                continue

            domain = rel_artifact["domain"]
            collection = chroma_client.get_collection(name=config.collection_name(domain))

            # Re-score related chunks against the actual query
            fetched = collection.query(
                query_texts=[query],
                n_results=min(3, len(chunk_ids)),  # limit chunks per related artifact
                where={"artifact_id": rel_artifact["id"]},
                include=["documents", "metadatas", "distances"],
            )

            if not fetched["ids"] or not fetched["ids"][0]:
                continue

            for i, chunk_id in enumerate(fetched["ids"][0]):
                if chunk_id in existing_ids:
                    continue

                distance = fetched["distances"][0][i] if fetched["distances"] else 1.0
                raw_relevance = max(0.0, min(1.0, 1.0 - distance))
                # Apply graph score factor — related content scores lower than direct hits
                depth_penalty = 1.0 / (1.0 + rel_artifact.get("relationship_depth", 1))
                relevance = round(
                    raw_relevance * config.GRAPH_RELATED_SCORE_FACTOR * depth_penalty, 4
                )

                metadata = fetched["metadatas"][0][i] if fetched["metadatas"] else {}

                graph_results.append({
                    "content": fetched["documents"][0][i],
                    "relevance": relevance,
                    "artifact_id": rel_artifact["id"],
                    "filename": rel_artifact["filename"],
                    "domain": domain,
                    "chunk_index": metadata.get("chunk_index", 0),
                    "collection": config.collection_name(domain),
                    "chunk_id": chunk_id,
                    "graph_source": True,
                    "relationship_type": rel_artifact.get("relationship_type", ""),
                    "relationship_reason": rel_artifact.get("relationship_reason", ""),
                })
                existing_ids.add(chunk_id)

        except Exception as e:
            logger.debug(f"Failed to fetch chunks for related artifact {rel_artifact['id'][:8]}: {e}")
            continue

    if graph_results:
        logger.info(f"Graph expansion added {len(graph_results)} related chunk(s)")

    return results + graph_results


# ---------------------------------------------------------------------------
# Reranking
# ---------------------------------------------------------------------------

async def rerank_results(
    results: list[dict[str, Any]],
    query: str,
    use_llm: bool = True
) -> list[dict[str, Any]]:
    """Rerank results via Bifrost LLM. Falls back to embedding sort on failure."""
    if not use_llm or len(results) == 0:
        return sorted(results, key=lambda x: x["relevance"], reverse=True)

    results = sorted(results, key=lambda x: x["relevance"], reverse=True)

    candidates = results[:config.QUERY_RERANK_CANDIDATES]
    remainder = results[config.QUERY_RERANK_CANDIDATES:]

    if len(candidates) <= 1:
        return results

    snippets = []
    for i, r in enumerate(candidates):
        preview = r["content"][:200].replace("\n", " ").strip()
        snippets.append(f"[{i}] ({r['domain']}/{r['filename']}) {preview}")

    prompt = (
        f"Given the query: \"{query}\"\n\n"
        f"Rank these document snippets by relevance to the query. "
        f"Return ONLY a JSON array of indices in order of most to least relevant.\n\n"
        + "\n".join(snippets)
        + f"\n\nRespond with ONLY a JSON array like [2, 0, 5, 1, ...] containing all indices 0-{len(candidates)-1}."
    )

    breaker = get_breaker("bifrost-rerank")

    async def _bifrost_rerank() -> dict:
        async with httpx.AsyncClient(timeout=config.BIFROST_TIMEOUT, headers=tracing_headers()) as client:
            resp = await client.post(
                f"{BIFROST_URL}/chat/completions",
                json={
                    "model": config.LLM_INTERNAL_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": 200,
                },
            )
            resp.raise_for_status()
            return resp.json()

    try:
        data = await breaker.call(_bifrost_rerank)

        content = data["choices"][0]["message"]["content"].strip()
        ranking = parse_llm_json(content)

        if not isinstance(ranking, list):
            raise ValueError("Expected a list of indices")

        valid_indices = set(range(len(candidates)))
        seen = set()
        reranked = []
        for idx in ranking:
            if isinstance(idx, int) and idx in valid_indices and idx not in seen:
                seen.add(idx)
                reranked.append(candidates[idx])

        for i, r in enumerate(candidates):
            if i not in seen:
                reranked.append(r)

        # Blend LLM rank score with original embedding score
        for rank_pos, result in enumerate(reranked):
            llm_score = 1.0 - (rank_pos / len(reranked))
            original_score = result["relevance"]
            result["relevance"] = round(
                config.RERANK_LLM_WEIGHT * llm_score
                + config.RERANK_ORIGINAL_WEIGHT * original_score,
                4,
            )

        return reranked + remainder

    except CircuitOpenError:
        logger.warning("Bifrost rerank circuit open, falling back to embedding sort")
        return sorted(results, key=lambda x: x["relevance"], reverse=True)
    except Exception as e:
        logger.warning(f"LLM reranking failed, falling back to embedding sort: {e}")
        return sorted(results, key=lambda x: x["relevance"], reverse=True)


# ---------------------------------------------------------------------------
# Metadata boost
# ---------------------------------------------------------------------------

def apply_metadata_boost(
    results: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    """Boost results whose tags or sub_category match query terms.

    Small additive boost for metadata alignment, capped at
    QUALITY_METADATA_MAX_BOOST to prevent tag-stuffed artifacts
    from dominating.
    """
    if not results:
        return results

    query_terms = {w.lower() for w in _WORD_RE.findall(query) if len(w) > 2}
    query_terms -= _STOPWORDS

    if not query_terms:
        return results

    for r in results:
        boost = 0.0

        # Sub-category match
        sub_cat = r.get("sub_category", "")
        if sub_cat:
            sub_cat_terms = {t.lower() for t in _WORD_RE.findall(sub_cat)}
            if sub_cat_terms & query_terms:
                boost += config.QUALITY_METADATA_SUBCAT_BOOST

        # Tag match
        tags_json = r.get("tags_json", "[]")
        try:
            tags = json.loads(tags_json) if tags_json else []
        except (json.JSONDecodeError, TypeError):
            tags = []
        for tag in tags:
            tag_terms = {t.lower() for t in _WORD_RE.findall(tag)}
            if tag_terms & query_terms:
                boost += config.QUALITY_METADATA_TAG_BOOST

        # Keyword match (lighter — keywords already used by BM25)
        kw_json = r.get("keywords", "[]")
        try:
            kw_list = json.loads(kw_json) if kw_json else []
        except (json.JSONDecodeError, TypeError):
            kw_list = []
        kw_matches = sum(1 for kw in kw_list if kw.lower() in query_terms)
        if kw_matches > 0:
            boost += min(kw_matches * 0.02, 0.06)

        boost = min(boost, config.QUALITY_METADATA_MAX_BOOST)
        if boost > 0:
            r["relevance"] = round(r["relevance"] + boost, 4)
            r["metadata_boost"] = round(boost, 4)

    return results


# ---------------------------------------------------------------------------
# Context alignment boost
# ---------------------------------------------------------------------------

def apply_context_alignment_boost(
    results: list[dict[str, Any]],
    conversation_messages: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Boost results whose content aligns with recent conversation context.

    Extracts key terms from conversation messages and computes what proportion
    appear in each result's content. More term overlap = higher boost.
    Applied after metadata boost, before reranking.
    """
    if not results or not conversation_messages:
        return results

    # Extract all meaningful terms from conversation
    context_terms: set = set()
    for msg in conversation_messages:
        if msg.get("role") == "user":
            words = _WORD_RE.findall(msg.get("content", ""))
            for word in words:
                lower = word.lower()
                if len(lower) > 2 and lower not in _STOPWORDS:
                    context_terms.add(lower)

    if not context_terms:
        return results

    boost_weight = config.CONTEXT_BOOST_WEIGHT

    for r in results:
        content_terms = {w.lower() for w in _WORD_RE.findall(r.get("content", "")) if len(w) > 2}
        matches = context_terms & content_terms
        if matches:
            alignment = len(matches) / len(context_terms)
            boost = alignment * boost_weight
            r["relevance"] = round(r["relevance"] + boost, 4)
            r["context_alignment"] = round(alignment, 4)

    return results


# ---------------------------------------------------------------------------
# Quality boost
# ---------------------------------------------------------------------------

def apply_quality_boost(
    results: list[dict[str, Any]],
    neo4j_driver: Any | None = None,
) -> list[dict[str, Any]]:
    """Apply quality score multiplier to relevance scores.

    Formula: adjusted = relevance * (QUALITY_BOOST_BASE + QUALITY_BOOST_FACTOR * quality_score)
    Default:  adjusted = relevance * (0.8 + 0.2 * quality_score)

    This means quality=1.0 → 1.0x (no change), quality=0.0 → 0.8x (20% penalty).
    """
    if neo4j_driver is None or not results:
        return results

    artifact_ids = list({r["artifact_id"] for r in results if r.get("artifact_id")})
    if not artifact_ids:
        return results

    try:
        from db.neo4j.artifacts import get_quality_scores
        scores = get_quality_scores(neo4j_driver, artifact_ids)
    except Exception as e:
        logger.warning(f"Quality score lookup failed (skipping boost): {e}")
        return results

    for r in results:
        quality = scores.get(r.get("artifact_id", ""), 0.5)
        multiplier = config.QUALITY_BOOST_BASE + config.QUALITY_BOOST_FACTOR * quality
        r["relevance"] = round(r["relevance"] * multiplier, 4)
        r["quality_score"] = quality

    return results


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def assemble_context(
    results: list[dict[str, Any]],
    max_chars: int = 14000,
    max_chunks_per_artifact: int = 0,
) -> tuple[str, list[dict[str, Any]], int]:
    """Build context window from top results, respecting token budget.

    Limits chunks per artifact to promote source diversity.  A value of 0
    for *max_chunks_per_artifact* means use the global config default.
    """
    if max_chunks_per_artifact <= 0:
        max_chunks_per_artifact = config.CONTEXT_MAX_CHUNKS_PER_ARTIFACT

    context_parts: list[str] = []
    included_sources: list[dict[str, Any]] = []
    char_count = 0
    artifact_counts: dict[str, int] = defaultdict(int)

    for result in results:
        artifact_id = result["artifact_id"]

        # Skip if this artifact already has enough chunks in context
        if artifact_counts[artifact_id] >= max_chunks_per_artifact:
            continue

        content = result["content"]
        content_len = len(content)

        if char_count + content_len > max_chars:
            continue  # don't break — later smaller chunks may still fit

        context_parts.append(content)
        included_sources.append({
            "content": content[:200],  # Preview only
            "relevance": result["relevance"],
            "artifact_id": artifact_id,
            "filename": result["filename"],
            "domain": result["domain"],
            "chunk_index": result["chunk_index"],
        })
        char_count += content_len
        artifact_counts[artifact_id] += 1

    context = "\n\n".join(context_parts)
    return context, included_sources, char_count


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
) -> dict[str, Any]:
    """Execute multi-domain query with reranking, graph expansion, and context assembly."""
    search_query = query
    if conversation_messages:
        search_query = _enrich_query(
            query, conversation_messages, max_context_messages=config.QUERY_CONTEXT_MESSAGES,
        )
        if search_query != query:
            logger.info(f"Enriched query: {query!r} → {search_query!r}")

    # When querying from a chat flow (conversation_messages provided) and no
    # explicit domain filter was requested, exclude the "conversations" domain.
    # Feedback-ingested conversation turns would otherwise dominate results,
    # creating circular noise (same pattern as hallucination.py:87-89).
    effective_domains = domains
    if effective_domains is None and conversation_messages:
        effective_domains = [d for d in config.DOMAINS if d != "conversations"]

    results = await multi_domain_query(
        query=search_query,
        domains=effective_domains,
        top_k=top_k,
        chroma_client=chroma_client,
    )

    # Search adjacent domains at reduced weight when specific domains are requested
    if domains and set(domains) != set(DOMAINS):
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

    results = deduplicate_results(results)

    graph_count_before = len(results)
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
    results = await rerank_results(
        results=results,
        query=query,
        use_llm=use_reranking,
    )

    # Step 5.5: Quality boost — quality score multiplier after reranking
    results = apply_quality_boost(results, neo4j_driver=neo4j_driver)
    results = sorted(results, key=lambda x: x["relevance"], reverse=True)

    # Step 5.6: Filter low-relevance results below minimum threshold
    results = [r for r in results if r["relevance"] >= config.QUALITY_MIN_RELEVANCE_THRESHOLD]

    # Step 6: Assemble context
    context, sources, char_count = assemble_context(results, max_chars=config.QUERY_CONTEXT_MAX_CHARS)

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

    return {
        "context": context,
        "sources": sources,
        "confidence": round(confidence, 4),
        "domains_searched": domains if domains else DOMAINS,
        "total_results": len(results),
        "token_budget_used": char_count,
        "graph_results": graph_results_added,
        "results": results,
    }
