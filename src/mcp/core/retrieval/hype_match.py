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
* Pure merge logic — no store access.  Querying the HyPE collections and
  hydrating each hit to its parent chunk happen in the caller,
  ``core.agents.query_agent._augment_with_hype``, which also returns the
  content hits unchanged when a HyPE collection does not exist.
* HyPE hits carry ``source_chunk_id`` on the hit; we use it to map them back
  to the parent chunk so they're de-duplicated and scored correctly against
  content hits. A hit's ``metadata`` is the parent chunk's custom ingest
  metadata, exactly as on a content hit, so linkage is never read from it.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("ai-companion.hype_match")

# Keys a hydrated HyPE hit carries only so this merge can find its parent.
_HYPE_LINKAGE_KEYS = frozenset({"source_chunk_id"})


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def dedup_with_hype_results(
    content_hits: list[dict[str, Any]],
    hype_hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge two relevance-ordered hit lists, keeping the best score per chunk.

    HyPE hits are mapped back to their parent chunk via ``source_chunk_id``.
    If the parent chunk is already in ``content_hits``, the higher-relevance
    occurrence wins.  If the parent chunk is *not* in content_hits, the HyPE
    hit is added as a content hit for that parent, keeping the fields it was
    hydrated with (``source_type``, lineage, parent linkage, custom
    ``metadata``) and tagged ``hype_source=True``.

    Parameters
    ----------
    content_hits:
        Ordered list of content-embedding hits (standard query_agent format).
    hype_hits:
        Ordered list of HyPE-embedding hits from the parallel collection.
        Each hit must carry ``source_chunk_id`` on the hit.  A hit without one
        cannot be tied to a parent chunk and is dropped with a warning; its
        ``chunk_id`` is not a substitute, and neither is a ``source_chunk_id``
        key in its ``metadata``, which belongs to the consumer.

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
    unlinked = 0

    for hype_hit in hype_hits:
        source_chunk_id = hype_hit.get("source_chunk_id", "")
        hype_relevance = hype_hit.get("relevance", 0.0)

        if source_chunk_id and source_chunk_id in content_by_chunk:
            # Parent is already in results — keep highest relevance.
            existing = content_by_chunk[source_chunk_id]
            if hype_relevance > existing.get("relevance", 0.0):
                existing["relevance"] = hype_relevance
                existing["hype_boosted"] = True
        elif source_chunk_id:
            # HyPE found a chunk not in content results — add it, keeping every
            # field it was hydrated with: the read filters that run after this
            # merge need its lineage and parent linkage, and later stages its
            # source_type and custom metadata. Only the HyPE-side linkage keys
            # are dropped.
            new_hit: dict[str, Any] = {
                "content": "",
                "filename": "",
                "domain": "",
                "chunk_index": 0,
                "collection": "",
                "ingested_at": "",
                "sub_category": "",
                "tags_json": "[]",
                "keywords": "[]",
                "memory_type": "",
                "source_type": "kb",
                "metadata": {},
                **{k: v for k, v in hype_hit.items() if k not in _HYPE_LINKAGE_KEYS},
                "relevance": hype_relevance,
                "artifact_id": hype_hit.get("artifact_id", ""),
                "chunk_id": source_chunk_id,
                "hype_source": True,
            }
            merged.append(new_hit)
            content_by_chunk[source_chunk_id] = new_hit
        else:
            unlinked += 1

    if unlinked:
        logger.warning(
            "dedup_with_hype_results: dropped %d HyPE hit(s) with no source_chunk_id", unlinked,
        )

    merged.sort(key=lambda x: x.get("relevance", 0.0), reverse=True)
    return merged
