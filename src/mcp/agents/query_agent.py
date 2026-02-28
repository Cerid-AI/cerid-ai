# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Multi-domain knowledge base search with LLM reranking."""

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

import chromadb
import httpx

import config
from config import BIFROST_URL, CHROMA_URL, DOMAINS
from deps import parse_chroma_url
from utils.cache import log_event
from utils.llm_parsing import parse_llm_json

logger = logging.getLogger("ai-companion.query_agent")


# ---------------------------------------------------------------------------
# Cross-domain affinity
# ---------------------------------------------------------------------------

def _get_adjacent_domains(requested: List[str]) -> Dict[str, float]:
    """Return non-requested domains with their max affinity score."""
    requested_set = set(requested)
    adjacent: Dict[str, float] = {}
    for req in requested:
        explicit = config.DOMAIN_AFFINITY.get(req, {})
        for other in DOMAINS:
            if other in requested_set:
                continue
            weight = explicit.get(other, config.CROSS_DOMAIN_DEFAULT_AFFINITY)
            adjacent[other] = max(adjacent.get(other, 0.0), weight)
    return adjacent


# ---------------------------------------------------------------------------
# Multi-domain retrieval
# ---------------------------------------------------------------------------

async def multi_domain_query(
    query: str,
    domains: Optional[List[str]] = None,
    top_k: int = 10,
    chroma_client: Optional[chromadb.HttpClient] = None
) -> List[Dict[str, Any]]:
    """Query multiple ChromaDB collections in parallel and aggregate results."""
    if domains is None:
        domains = DOMAINS

    invalid_domains = [d for d in domains if d not in DOMAINS]
    if invalid_domains:
        raise ValueError(f"Invalid domains: {invalid_domains}. Valid: {DOMAINS}")

    if chroma_client is None:
        host, port = parse_chroma_url()
        chroma_client = chromadb.HttpClient(host=host, port=port)

    async def query_domain(domain: str) -> List[Dict[str, Any]]:
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

                    formatted.append({
                        "content": results["documents"][0][i],
                        "relevance": round(relevance, 4),
                        "artifact_id": metadata.get("artifact_id", ""),
                        "filename": metadata.get("filename", ""),
                        "domain": domain,
                        "chunk_index": metadata.get("chunk_index", 0),
                        "collection": config.collection_name(domain),
                        "chunk_id": chunk_id,
                        "ingested_at": metadata.get("ingested_at", ""),
                    })
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
                                formatted.append({
                                    "content": fetched["documents"][j],
                                    "relevance": round(
                                        config.HYBRID_KEYWORD_WEIGHT * bm25_map[cid], 4
                                    ),
                                    "artifact_id": meta.get("artifact_id", ""),
                                    "filename": meta.get("filename", ""),
                                    "domain": domain,
                                    "chunk_index": meta.get("chunk_index", 0),
                                    "collection": config.collection_name(domain),
                                    "chunk_id": cid,
                                    "ingested_at": meta.get("ingested_at", ""),
                                })
                                seen_ids.add(cid)
                        except Exception as e:
                            logger.debug(f"BM25-only fetch failed for {domain}: {e}")

            return formatted

        except Exception as e:
            logger.warning(f"Error querying domain {domain}: {e}")
            return []

    tasks = [query_domain(domain) for domain in domains]
    domain_results = await asyncio.gather(*tasks)

    all_results = []
    for results in domain_results:
        all_results.extend(results)

    return all_results


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
    results: List[Dict[str, Any]],
    query: str,
    chroma_client: Optional[chromadb.HttpClient] = None,
    neo4j_driver: Optional[Any] = None,
) -> List[Dict[str, Any]]:
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
        chroma_client = chromadb.HttpClient(
            host=CHROMA_URL.replace("http://", "").split(":")[0],
            port=int(CHROMA_URL.split(":")[-1]),
        )

    existing_ids = {r.get("chunk_id") for r in results}
    graph_results: List[Dict[str, Any]] = []

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
    results: List[Dict[str, Any]],
    query: str,
    use_llm: bool = True
) -> List[Dict[str, Any]]:
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

    try:
        async with httpx.AsyncClient(timeout=config.BIFROST_TIMEOUT) as client:
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
            data = resp.json()

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

    except Exception as e:
        logger.warning(f"LLM reranking failed, falling back to embedding sort: {e}")
        return sorted(results, key=lambda x: x["relevance"], reverse=True)


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def assemble_context(
    results: List[Dict[str, Any]],
    max_chars: int = 14000
) -> tuple[str, List[Dict[str, Any]], int]:
    """Build context window from top results, respecting token budget."""
    context_parts = []
    included_sources = []
    char_count = 0

    for result in results:
        content = result["content"]
        content_len = len(content)

        if char_count + content_len > max_chars:
            break

        context_parts.append(content)
        included_sources.append({
            "content": content[:200],  # Preview only
            "relevance": result["relevance"],
            "artifact_id": result["artifact_id"],
            "filename": result["filename"],
            "domain": result["domain"],
            "chunk_index": result["chunk_index"]
        })
        char_count += content_len

    context = "\n\n".join(context_parts)
    return context, included_sources, char_count


# ---------------------------------------------------------------------------
# Main query agent function
# ---------------------------------------------------------------------------

async def agent_query(
    query: str,
    domains: Optional[List[str]] = None,
    top_k: int = 10,
    use_reranking: bool = True,
    chroma_client: Optional[chromadb.HttpClient] = None,
    redis_client: Optional[Any] = None,
    neo4j_driver: Optional[Any] = None,
) -> Dict[str, Any]:
    """Execute multi-domain query with reranking, graph expansion, and context assembly."""
    results = await multi_domain_query(
        query=query,
        domains=domains,
        top_k=top_k,
        chroma_client=chroma_client,
    )

    # Search adjacent domains at reduced weight when specific domains are requested
    if domains and set(domains) != set(DOMAINS):
        adjacent = _get_adjacent_domains(domains)
        if adjacent:
            cross_results = await multi_domain_query(
                query=query,
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

    # Step 5: Reranking (includes both direct and graph-sourced results)
    results = await rerank_results(
        results=results,
        query=query,
        use_llm=use_reranking,
    )

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

    # Return structured response
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
