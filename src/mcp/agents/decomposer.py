# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Query decomposition — splits complex queries into sub-queries for parallel retrieval.

Dependencies: utils/llm_client.py. Error types: RetrievalError.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import config
from config import DOMAINS
from deps import get_chroma
from errors import RetrievalError
from utils.circuit_breaker import get_breaker
from utils.text import STOPWORDS as _STOPWORDS
from utils.text import WORD_RE as _WORD_RE

logger = logging.getLogger("ai-companion.query_agent")

__all__ = [
    "_get_adjacent_domains",
    "_enrich_query",
    "multi_domain_query",
    "lightweight_kb_query",
    "graph_expand_results",
]


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
    from utils.retrieval_profile import deserialize_profile

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
        "retrieval_profile": deserialize_profile(metadata.get("retrieval_profile")),
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

    # Pre-check which collections actually exist to skip missing domains fast
    try:
        existing_collections = {c.name for c in chroma_client.list_collections()}
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        existing_collections = set()

    async def query_domain(domain: str) -> list[dict[str, Any]]:
        """Query a single domain collection (vector + BM25 hybrid)."""
        col_name = config.collection_name(domain)
        if existing_collections and col_name not in existing_collections:
            return []  # Skip missing collections without HTTP round-trip
        try:
            collection = chroma_client.get_collection(name=col_name)

            chromadb_breaker = get_breaker("chromadb")
            results = await chromadb_breaker.call(
                lambda: asyncio.to_thread(
                    collection.query,
                    query_texts=[query],
                    n_results=top_k,
                    include=["documents", "metadatas", "distances"],
                )
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
                try:
                    bm25_hits = await asyncio.wait_for(
                        asyncio.to_thread(bm25_mod.search_bm25, domain, query, top_k),
                        timeout=2.0,  # 2 second max for BM25 per domain
                    )
                except asyncio.TimeoutError:
                    logger.warning("BM25 search timed out for domain %s", domain)
                    bm25_hits = None
                if bm25_hits:
                    bm25_map = dict(bm25_hits)

                    from utils.retrieval_profile import get_hybrid_weights

                    for entry in formatted:
                        kw_score = bm25_map.pop(entry["chunk_id"], 0.0)
                        vector_score = entry["relevance"]
                        # Use per-chunk profile to adjust hybrid weights
                        vw, kw_w = get_hybrid_weights(
                            entry.get("retrieval_profile"),
                            config.HYBRID_VECTOR_WEIGHT,
                            config.HYBRID_KEYWORD_WEIGHT,
                        )
                        entry["relevance"] = round(
                            vw * vector_score + kw_w * kw_score,
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
                        except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
                            logger.debug(f"BM25-only fetch failed for {domain}: {e}")

            return formatted

        except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
            logger.warning(f"Error querying domain {domain}: {e}")
            return []

    tasks = [query_domain(domain) for domain in domains]
    domain_results = await asyncio.gather(*tasks)

    all_results = [r for results in domain_results for r in results]

    from utils.agent_events import emit_agent_event
    emit_agent_event("decomposer", f"Retrieved {len(all_results)} chunks across {len(domains)} domains")

    return all_results


# ---------------------------------------------------------------------------
# Lightweight retrieval (verification fast-path)
# ---------------------------------------------------------------------------

async def lightweight_kb_query(
    query: str,
    domains: list[str] | None = None,
    top_k: int = 5,
    chroma_client: Any | None = None,
) -> list[dict[str, Any]]:
    """Fast KB retrieval for verification — vector + BM25 hybrid only.

    Skips: graph expansion, cross-encoder reranking, quality boost,
    MMR diversity, semantic cache, adaptive gate, query decomposition,
    context assembly.  Returns raw ranked results suitable for
    claim verification where only semantic similarity matters.
    """
    from agents.assembler import deduplicate_results

    results = await multi_domain_query(
        query, domains=domains, top_k=top_k, chroma_client=chroma_client,
    )
    results = deduplicate_results(results)
    results.sort(key=lambda x: x.get("relevance", 0.0), reverse=True)
    return results[:top_k]


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

    from db.neo4j import find_related_artifacts

    initial_ids = list({r["artifact_id"] for r in results if r.get("artifact_id")})
    if not initial_ids:
        return results

    try:
        related = await asyncio.to_thread(
            find_related_artifacts,
            neo4j_driver,
            artifact_ids=initial_ids,
            depth=config.GRAPH_TRAVERSAL_DEPTH,
            max_results=config.GRAPH_MAX_RELATED,
        )
    except (RetrievalError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning(f"Graph traversal failed (continuing without): {e}")
        return results

    if not related:
        return results

    if chroma_client is None:
        chroma_client = get_chroma()

    existing_ids = {r.get("chunk_id") for r in results}

    async def _fetch_related(rel_artifact: dict) -> list[dict[str, Any]]:
        """Fetch and score chunks for a single related artifact."""
        chunk_ids_json = rel_artifact.get("chunk_ids", "[]")
        chunk_ids = json.loads(chunk_ids_json) if chunk_ids_json else []
        if not chunk_ids:
            return []

        domain = rel_artifact["domain"]
        collection = chroma_client.get_collection(name=config.collection_name(domain))

        fetched = collection.query(
            query_texts=[query],
            n_results=min(3, len(chunk_ids)),
            where={"artifact_id": rel_artifact["id"]},
            include=["documents", "metadatas", "distances"],
        )

        if not fetched["ids"] or not fetched["ids"][0]:
            return []

        chunks: list[dict[str, Any]] = []
        for i, chunk_id in enumerate(fetched["ids"][0]):
            distance = fetched["distances"][0][i] if fetched["distances"] else 1.0
            raw_relevance = max(0.0, min(1.0, 1.0 - distance))
            depth_penalty = 1.0 / (1.0 + rel_artifact.get("relationship_depth", 1))
            relevance = round(
                raw_relevance * config.GRAPH_RELATED_SCORE_FACTOR * depth_penalty, 4
            )
            metadata = fetched["metadatas"][0][i] if fetched["metadatas"] else {}
            chunks.append({
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
        return chunks

    # Fetch chunks for all related artifacts in parallel
    tasks = [_fetch_related(ra) for ra in related]
    all_fetched = await asyncio.gather(*tasks, return_exceptions=True)

    graph_results: list[dict[str, Any]] = []
    for batch in all_fetched:
        if isinstance(batch, BaseException):
            logger.debug("Failed to fetch chunks for related artifact: %s", batch)
            continue
        for chunk in batch:
            if chunk["chunk_id"] not in existing_ids:
                graph_results.append(chunk)
                existing_ids.add(chunk["chunk_id"])

    if graph_results:
        logger.info(f"Graph expansion added {len(graph_results)} related chunk(s)")

    return results + graph_results
