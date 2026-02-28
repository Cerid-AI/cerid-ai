# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Semantic deduplication — embedding-based near-duplicate detection (Pro tier)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger("ai-companion.dedup")

NEAR_DUPLICATE_THRESHOLD = 0.92
_MAX_EMBED_CHARS = 2000


def check_semantic_duplicate(
    text: str,
    domain: str,
    chroma_client: Any,
    exclude_artifact_id: str = "",
    threshold: float = NEAR_DUPLICATE_THRESHOLD,
) -> Optional[Dict[str, Any]]:
    """Check for near-duplicates in the same domain via ChromaDB embedding similarity."""
    if not text or not text.strip():
        return None

    snippet = text[:_MAX_EMBED_CHARS]

    coll_name = config.collection_name(domain)

    try:
        collection = chroma_client.get_or_create_collection(name=coll_name)

        count = collection.count()
        if count == 0:
            return None

        results = collection.query(
            query_texts=[snippet],
            n_results=min(3, count),
            include=["metadatas", "distances"],
        )

        if not results or not results.get("ids") or not results["ids"][0]:
            return None

        # L2 distance -> approximate similarity: 1 / (1 + distance)
        for i, distance in enumerate(results["distances"][0]):
            similarity = 1.0 / (1.0 + distance)

            if similarity < threshold:
                continue

            meta = results["metadatas"][0][i] if results["metadatas"][0] else {}
            artifact_id = meta.get("artifact_id", "")

            if exclude_artifact_id and artifact_id == exclude_artifact_id:
                continue

            match = {
                "artifact_id": artifact_id,
                "filename": meta.get("filename", "unknown"),
                "similarity": round(similarity, 4),
                "domain": domain,
                "chunk_id": results["ids"][0][i],
            }

            logger.info(
                f"Near-duplicate detected: similarity={similarity:.3f} "
                f"with artifact {artifact_id[:8]} ('{meta.get('filename', '?')}') "
                f"in domain '{domain}'"
            )
            return match

    except Exception as e:
        logger.warning(f"Semantic dedup check failed (non-blocking): {e}")

    return None


def check_semantic_duplicate_batch(
    texts: List[str],
    domains: List[str],
    chroma_client: Any,
    threshold: float = NEAR_DUPLICATE_THRESHOLD,
) -> List[Optional[Dict[str, Any]]]:
    """Batch semantic duplicate check — one result per input."""
    results = []
    for text, domain in zip(texts, domains):
        result = check_semantic_duplicate(
            text=text,
            domain=domain,
            chroma_client=chroma_client,
            threshold=threshold,
        )
        results.append(result)
    return results
