# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""HyPE retrieval-time matching and deduplication.

Phase R.3.  At retrieval time, when ``RETRIEVAL_HYPE_ENABLED=true``, the
query is matched against *both* the primary content embeddings and the HyPE
question embeddings that were pre-computed at index time.  This module
provides:

* :func:`dedup_with_hype_results` — merges two ranked hit lists, keeping
  the highest-relevance occurrence of each chunk.

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

import logging
from typing import Any

logger = logging.getLogger("ai-companion.hype_match")


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
