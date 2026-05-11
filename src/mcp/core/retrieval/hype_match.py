# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""HyPE retrieval-time matching and deduplication.

Phase R.3.  At retrieval time, when ``RETRIEVAL_HYPE_ENABLED=true``, the
query is matched against *both* the primary content embeddings and the HyPE
question embeddings that were pre-computed at index time.  This module
provides:

* :func:`dedup_with_hype_results` — merges two ranked hit lists, keeping
  the highest-relevance occurrence of each chunk.
* :func:`query_with_hype` — issues the two Chroma queries and calls dedup.

Design notes
------------
* Pure logic layer — no app imports.  Chroma client and embed function are
  injected.
* The HyPE collection name is derived from :func:`hype_index.hype_collection_name`.
* HyPE hits carry ``source_chunk_id`` and ``source_artifact_id`` in their
  metadata; we use those to map them back to the parent chunk representation
  so they're de-duplicated and scored correctly against content hits.
* When the HyPE collection doesn't exist (e.g. a corpus that was indexed
  before R.3 or with the flag off) the function returns content hits
  unchanged — HyPE is always additive, never a regression path.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger("ai-companion.hype_match")

# Type aliases
EmbedFn = Callable[[str], Awaitable[list[float]]]


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def dedup_with_hype_results(
    content_hits: list[dict[str, Any]],
    hype_hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge two relevance-ordered hit lists, keeping the best score per chunk.

    HyPE hits are mapped back to their parent chunk via
    ``source_chunk_id``/``source_artifact_id`` metadata.  If the parent chunk
    is already in ``content_hits``, the higher-relevance occurrence wins.  If
    the parent chunk is *not* in content_hits, the HyPE hit is re-shaped into
    a content-hit representation (with ``hype_source=True`` tag) and added.

    Parameters
    ----------
    content_hits:
        Ordered list of content-embedding hits (standard query_agent format).
    hype_hits:
        Ordered list of HyPE-embedding hits from the parallel collection.
        Each hit must carry ``source_chunk_id`` in its ``chunk_id`` field or
        metadata.

    Returns
    -------
    list[dict[str, Any]]
        Merged, de-duplicated list sorted by relevance descending.
    """
    # Build index of content hits keyed by chunk_id for O(1) lookup.
    # chunk_id is the canonical identifier in the query_agent result format.
    content_by_chunk: dict[str, dict[str, Any]] = {}
    for hit in content_hits:
        cid = hit.get("chunk_id") or hit.get("id", "")
        if cid:
            content_by_chunk[cid] = hit

    merged: list[dict[str, Any]] = list(content_hits)  # start with all content hits

    for hype_hit in hype_hits:
        # Extract the source (parent) chunk id.
        # The HyPE collection stores it in metadata["source_chunk_id"].
        meta = hype_hit.get("metadata") or {}
        source_chunk_id = meta.get("source_chunk_id") or hype_hit.get("source_chunk_id", "")
        hype_relevance = hype_hit.get("relevance", 0.0)

        if source_chunk_id and source_chunk_id in content_by_chunk:
            # Parent is already in results — keep highest relevance.
            existing = content_by_chunk[source_chunk_id]
            if hype_relevance > existing.get("relevance", 0.0):
                existing["relevance"] = hype_relevance
                existing["hype_boosted"] = True
        elif source_chunk_id:
            # HyPE found a chunk not in content results — add it.
            # Re-shape using whatever metadata the HyPE hit carries.
            new_hit: dict[str, Any] = {
                "content": hype_hit.get("content", ""),
                "relevance": hype_relevance,
                "artifact_id": meta.get("source_artifact_id", ""),
                "filename": hype_hit.get("filename", ""),
                "domain": hype_hit.get("domain", ""),
                "chunk_index": hype_hit.get("chunk_index", 0),
                "collection": hype_hit.get("collection", ""),
                "chunk_id": source_chunk_id,
                "ingested_at": hype_hit.get("ingested_at", ""),
                "sub_category": hype_hit.get("sub_category", ""),
                "tags_json": hype_hit.get("tags_json", "[]"),
                "keywords": hype_hit.get("keywords", "[]"),
                "memory_type": hype_hit.get("memory_type", ""),
                "hype_source": True,
            }
            merged.append(new_hit)
            content_by_chunk[source_chunk_id] = new_hit

    merged.sort(key=lambda x: x.get("relevance", 0.0), reverse=True)
    return merged


# ---------------------------------------------------------------------------
# Dual-query retrieval
# ---------------------------------------------------------------------------

async def query_with_hype(
    query: str,
    *,
    chroma: Any,
    embed_fn: EmbedFn,
    collection_names: list[str],
    n_results: int = 10,
    where: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Issue two Chroma queries — content embeddings and HyPE embeddings.

    Returns a ``(content_hits, hype_hits)`` tuple.  The caller is responsible
    for merging them via :func:`dedup_with_hype_results`.  Returning both
    lists separately allows the caller to observe the contribution of each
    path for observability purposes.

    Parameters
    ----------
    query:
        The raw user query string.
    chroma:
        ChromaDB client (sync; calls are offloaded to a thread).
    embed_fn:
        Async callable ``(text) → embedding_vector`` for the query.
    collection_names:
        List of base collection names to query (e.g. ``["cerid_general"]``).
        The parallel HyPE collections are derived via
        :func:`hype_index.hype_collection_name`.
    n_results:
        Number of results to fetch per collection from each query type.
    where:
        Optional ChromaDB ``where`` filter applied to both query types.

    Returns
    -------
    tuple[list[dict], list[dict]]
        ``(content_hits, hype_hits)`` — both in the standard query_agent
        result format.  ``hype_hits`` entries carry ``source_chunk_id`` in
        their metadata dict.
    """
    from core.retrieval.hype_index import hype_collection_name
    from core.utils.embeddings import l2_distance_to_relevance

    # Embed the query once — reused for both content and HyPE queries.
    query_embedding: list[float] = await embed_fn(query)

    content_hits: list[dict[str, Any]] = []
    hype_hits: list[dict[str, Any]] = []

    for coll_name in collection_names:
        # --- Content query -----------------------------------------------
        try:
            content_coll = chroma.get_collection(name=coll_name)
            query_kwargs: dict[str, Any] = {
                "query_embeddings": [query_embedding],
                "n_results": n_results,
                "include": ["documents", "metadatas", "distances"],
            }
            if where is not None:
                query_kwargs["where"] = where
            results = await asyncio.to_thread(content_coll.query, **query_kwargs)

            if results["ids"] and results["ids"][0]:
                for i, chunk_id in enumerate(results["ids"][0]):
                    distance = results["distances"][0][i] if results["distances"] else 1.0
                    relevance = l2_distance_to_relevance(distance)
                    meta = results["metadatas"][0][i] if results["metadatas"] else {}
                    content_hits.append({
                        "content": results["documents"][0][i],
                        "relevance": round(relevance, 4),
                        "artifact_id": meta.get("artifact_id", ""),
                        "filename": meta.get("filename", ""),
                        "domain": meta.get("domain", ""),
                        "chunk_index": meta.get("chunk_index", 0),
                        "collection": coll_name,
                        "chunk_id": chunk_id,
                        "ingested_at": meta.get("ingested_at", ""),
                        "sub_category": meta.get("sub_category", ""),
                        "tags_json": meta.get("tags_json", "[]"),
                        "keywords": meta.get("keywords", "[]"),
                        "memory_type": meta.get("memory_type", ""),
                    })
        except Exception as e:
            logger.warning("hype_match.content_query failed for %s: %s", coll_name, e)

        # --- HyPE query --------------------------------------------------
        hype_coll_name = hype_collection_name(coll_name)
        try:
            hype_coll = chroma.get_collection(name=hype_coll_name)
            hype_query_kwargs: dict[str, Any] = {
                "query_embeddings": [query_embedding],
                "n_results": n_results,
                "include": ["documents", "metadatas", "distances"],
            }
            # HyPE collection has different metadata schema — don't apply
            # content-collection where filters (e.g. cerid_state) directly.
            # The parent chunk state is enforced when the caller resolves
            # the source chunk from the primary collection.
            hype_results = await asyncio.to_thread(hype_coll.query, **hype_query_kwargs)

            if hype_results["ids"] and hype_results["ids"][0]:
                for i, hype_doc_id in enumerate(hype_results["ids"][0]):
                    distance = hype_results["distances"][0][i] if hype_results["distances"] else 1.0
                    relevance = l2_distance_to_relevance(distance)
                    meta = hype_results["metadatas"][0][i] if hype_results["metadatas"] else {}
                    hype_hits.append({
                        "content": hype_results["documents"][0][i],
                        "relevance": round(relevance, 4),
                        "chunk_id": hype_doc_id,
                        "source_chunk_id": meta.get("source_chunk_id", ""),
                        "artifact_id": meta.get("source_artifact_id", ""),
                        "filename": "",
                        "domain": "",
                        "chunk_index": 0,
                        "collection": hype_coll_name,
                        "ingested_at": "",
                        "sub_category": "",
                        "tags_json": "[]",
                        "keywords": "[]",
                        "memory_type": "",
                        "metadata": meta,  # carry full meta for dedup logic
                    })
        except Exception as e:
            # HyPE collection missing (flag was off at index time) — not an
            # error, just means HyPE has no data for this collection.
            logger.debug(
                "hype_match.hype_query: collection %s not found or failed: %s",
                hype_coll_name, e,
            )

    return content_hits, hype_hits
