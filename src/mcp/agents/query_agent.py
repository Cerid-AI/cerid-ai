"""
Query Agent - Multi-domain knowledge base search with intelligent reranking.

Provides enhanced query capabilities:
- Multi-domain parallel retrieval across all ChromaDB collections
- Deduplication by artifact_id + chunk_index
- LLM-powered reranking for improved relevance
- Token budget enforcement (14k char limit)
- Source attribution with confidence scoring
"""

import asyncio
from typing import List, Dict, Optional, Any
from collections import defaultdict

import chromadb
from chromadb.config import Settings
import httpx

from config import DOMAINS, BIFROST_URL, CHROMA_URL
from utils.cache import log_event


# ---------------------------------------------------------------------------
# Multi-domain retrieval
# ---------------------------------------------------------------------------

async def multi_domain_query(
    query: str,
    domains: Optional[List[str]] = None,
    top_k: int = 10,
    chroma_client: Optional[chromadb.HttpClient] = None
) -> List[Dict[str, Any]]:
    """
    Query multiple ChromaDB collections in parallel and aggregate results.

    Args:
        query: Natural language search query
        domains: List of domains to search (default: all domains)
        top_k: Number of results to retrieve per domain
        chroma_client: Existing ChromaDB client (or create new one)

    Returns:
        List of results with metadata:
        [
            {
                "content": "chunk text...",
                "relevance": 0.85,
                "artifact_id": "uuid",
                "filename": "example.pdf",
                "domain": "finance",
                "chunk_index": 3,
                "collection": "domain_finance"
            },
            ...
        ]
    """
    # Default to all domains if none specified
    if domains is None:
        domains = DOMAINS

    # Validate domains
    invalid_domains = [d for d in domains if d not in DOMAINS]
    if invalid_domains:
        raise ValueError(f"Invalid domains: {invalid_domains}. Valid: {DOMAINS}")

    # Connect to ChromaDB if client not provided
    if chroma_client is None:
        chroma_client = chromadb.HttpClient(
            host=CHROMA_URL.replace("http://", "").split(":")[0],
            port=int(CHROMA_URL.split(":")[-1])
        )

    # Query each domain collection in parallel
    async def query_domain(domain: str) -> List[Dict[str, Any]]:
        """Query a single domain collection."""
        try:
            collection_name = f"domain_{domain}"
            collection = chroma_client.get_collection(name=collection_name)

            # Execute query
            results = collection.query(
                query_texts=[query],
                n_results=top_k,
                include=["documents", "metadatas", "distances"]
            )

            # Format results
            formatted = []
            if results["ids"] and results["ids"][0]:
                for i, chunk_id in enumerate(results["ids"][0]):
                    # Calculate relevance score (1.0 - cosine_distance, clamped 0-1)
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
                        "collection": collection_name,
                        "chunk_id": chunk_id
                    })

            return formatted

        except Exception as e:
            # Log error but don't fail entire query
            print(f"Error querying domain {domain}: {e}")
            return []

    # Query all domains concurrently
    tasks = [query_domain(domain) for domain in domains]
    domain_results = await asyncio.gather(*tasks)

    # Flatten results
    all_results = []
    for results in domain_results:
        all_results.extend(results)

    return all_results


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Remove duplicate chunks (same artifact_id + chunk_index).
    Keep the result with highest relevance score.

    Args:
        results: List of query results

    Returns:
        Deduplicated list
    """
    # Group by (artifact_id, chunk_index)
    groups = defaultdict(list)
    for result in results:
        key = (result["artifact_id"], result["chunk_index"])
        groups[key].append(result)

    # Keep highest relevance from each group
    deduplicated = []
    for group in groups.values():
        # Sort by relevance descending, take first
        best = max(group, key=lambda x: x["relevance"])
        deduplicated.append(best)

    return deduplicated


# ---------------------------------------------------------------------------
# Reranking
# ---------------------------------------------------------------------------

async def rerank_results(
    results: List[Dict[str, Any]],
    query: str,
    use_llm: bool = True
) -> List[Dict[str, Any]]:
    """
    Rerank results using LLM-based relevance scoring via Bifrost.

    Sends the top candidates to a free LLM model for intelligent relevance
    scoring. Falls back to cosine-similarity sorting if the LLM call fails.

    Args:
        results: List of query results
        query: Original query string
        use_llm: Whether to use LLM reranking (or just sort by relevance)

    Returns:
        Reranked list (sorted by relevance descending)
    """
    if not use_llm or len(results) == 0:
        return sorted(results, key=lambda x: x["relevance"], reverse=True)

    # Pre-sort by embedding relevance first
    results = sorted(results, key=lambda x: x["relevance"], reverse=True)

    # Only rerank the top candidates (token-efficient)
    MAX_RERANK = 15
    candidates = results[:MAX_RERANK]
    remainder = results[MAX_RERANK:]

    if len(candidates) <= 1:
        return results

    # Build a compact prompt for the LLM
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
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{BIFROST_URL}/chat/completions",
                json={
                    "model": "meta-llama/llama-3.1-8b-instruct:free",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": 200,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"].strip()
        # Extract JSON array from response (handle markdown fences)
        if content.startswith("```"):
            content = content.split("\n", 1)[-1]
        if content.endswith("```"):
            content = content.rsplit("```", 1)[0]
        content = content.strip()

        import json
        ranking = json.loads(content)

        if not isinstance(ranking, list):
            raise ValueError("Expected a list of indices")

        # Validate and apply ranking
        valid_indices = set(range(len(candidates)))
        seen = set()
        reranked = []
        for idx in ranking:
            if isinstance(idx, int) and idx in valid_indices and idx not in seen:
                seen.add(idx)
                reranked.append(candidates[idx])

        # Append any candidates not mentioned in the ranking
        for i, r in enumerate(candidates):
            if i not in seen:
                reranked.append(r)

        # Update relevance scores to reflect LLM ranking order
        for rank_pos, result in enumerate(reranked):
            # Blend: 60% LLM rank score + 40% original embedding score
            llm_score = 1.0 - (rank_pos / len(reranked))
            original_score = result["relevance"]
            result["relevance"] = round(0.6 * llm_score + 0.4 * original_score, 4)

        return reranked + remainder

    except Exception as e:
        print(f"LLM reranking failed, falling back to embedding sort: {e}")
        return sorted(results, key=lambda x: x["relevance"], reverse=True)


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def assemble_context(
    results: List[Dict[str, Any]],
    max_chars: int = 14000
) -> tuple[str, List[Dict[str, Any]], int]:
    """
    Build context window from top results, respecting token budget.

    Args:
        results: Sorted list of query results
        max_chars: Maximum characters for context (default: 14k)

    Returns:
        (context_string, included_sources, char_count)
    """
    context_parts = []
    included_sources = []
    char_count = 0

    for result in results:
        content = result["content"]
        content_len = len(content)

        # Check if adding this chunk would exceed budget
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
    redis_client: Optional[Any] = None
) -> Dict[str, Any]:
    """
    Execute multi-domain query with intelligent context assembly.

    Args:
        query: Natural language search query
        domains: Optional list of domains to search (default: all)
        top_k: Number of results per domain
        use_reranking: Enable LLM-based reranking
        chroma_client: Existing ChromaDB client
        redis_client: Existing Redis client for audit logging

    Returns:
        {
            "context": "assembled context string...",
            "sources": [{...}, ...],
            "confidence": 0.85,
            "domains_searched": ["coding", "finance"],
            "total_results": 42,
            "token_budget_used": 12500,
            "results": [{...}, ...]  # Full result list
        }
    """
    # Step 1: Multi-domain retrieval
    results = await multi_domain_query(
        query=query,
        domains=domains,
        top_k=top_k,
        chroma_client=chroma_client
    )

    # Step 2: Deduplication
    results = deduplicate_results(results)

    # Step 3: Reranking
    results = await rerank_results(
        results=results,
        query=query,
        use_llm=use_reranking
    )

    # Step 4: Assemble context
    context, sources, char_count = assemble_context(results, max_chars=14000)

    # Step 5: Calculate confidence (average relevance of included sources)
    confidence = 0.0
    if sources:
        confidence = sum(s["relevance"] for s in sources) / len(sources)

    # Step 6: Log query (optional)
    if redis_client:
        try:
            log_event(
                redis_client,
                event_type="query",
                artifact_id="",
                domain=",".join(domains) if domains else "all",
                filename="",
                extra={"query": query, "results": len(results)}
            )
        except Exception as e:
            print(f"Failed to log query: {e}")

    # Return structured response
    return {
        "context": context,
        "sources": sources,
        "confidence": round(confidence, 4),
        "domains_searched": domains if domains else DOMAINS,
        "total_results": len(results),
        "token_budget_used": char_count,
        "results": results  # Full result list for debugging
    }
