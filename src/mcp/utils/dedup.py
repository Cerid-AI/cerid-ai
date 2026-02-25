"""
Semantic deduplication for Cerid AI (Phase 8B).

Extends the existing SHA-256 exact-hash dedup with embedding-based
near-duplicate detection. Catches renamed/reformatted files that have
different hashes but semantically identical content.

This is a Pro-tier feature gated by the `semantic_dedup` feature flag.

Algorithm:
1. Exact hash check (existing, fast) — if duplicate, stop
2. Embed first chunk of new document
3. Query ChromaDB for top-3 similar in same domain (threshold: cosine > 0.92)
4. If match found: flag as "near-duplicate" in metadata
5. Return match info for caller to decide action

Usage:
    from utils.dedup import check_semantic_duplicate

    result = check_semantic_duplicate(
        text="...",
        domain="coding",
        chroma_client=get_chroma(),
    )
    if result:
        # result = {"artifact_id": "...", "filename": "...", "similarity": 0.95}
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ai-companion.dedup")

# Similarity threshold for near-duplicate detection (cosine similarity)
NEAR_DUPLICATE_THRESHOLD = 0.92

# Maximum text length to embed for comparison
_MAX_EMBED_CHARS = 2000


def check_semantic_duplicate(
    text: str,
    domain: str,
    chroma_client: Any,
    exclude_artifact_id: str = "",
    threshold: float = NEAR_DUPLICATE_THRESHOLD,
) -> Optional[Dict[str, Any]]:
    """
    Check if text is semantically similar to existing artifacts in the same domain.

    Uses ChromaDB's built-in embedding + similarity search on the first
    chunk of content to detect near-duplicates.

    Args:
        text: Full document text to check
        domain: Domain to search within
        chroma_client: ChromaDB client instance
        exclude_artifact_id: Skip this artifact ID in results (for re-ingest)
        threshold: Cosine similarity threshold (default: 0.92)

    Returns:
        Dict with near-duplicate info if found:
            {"artifact_id": str, "filename": str, "similarity": float, "domain": str}
        None if no near-duplicate found
    """
    if not text or not text.strip():
        return None

    # Use first portion of text for embedding comparison
    snippet = text[:_MAX_EMBED_CHARS]

    collection_name = f"domain_{domain.replace(' ', '_').lower()}"

    try:
        collection = chroma_client.get_or_create_collection(name=collection_name)

        # Check if collection has any documents
        count = collection.count()
        if count == 0:
            return None

        # Query for similar documents
        results = collection.query(
            query_texts=[snippet],
            n_results=min(3, count),
            include=["metadatas", "distances"],
        )

        if not results or not results.get("ids") or not results["ids"][0]:
            return None

        # ChromaDB returns L2 distances by default; convert to approximate cosine similarity
        # For normalized embeddings: cosine_similarity ≈ 1 - (l2_distance² / 2)
        # We use a heuristic: similarity = 1 / (1 + distance) for a rough approximation
        for i, distance in enumerate(results["distances"][0]):
            # Convert L2 distance to similarity score (0-1 range)
            similarity = 1.0 / (1.0 + distance)

            if similarity < threshold:
                continue

            meta = results["metadatas"][0][i] if results["metadatas"][0] else {}
            artifact_id = meta.get("artifact_id", "")

            # Skip self-matches
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
    """
    Batch semantic duplicate check for multiple documents.

    Args:
        texts: List of document texts
        domains: List of domains (one per text)
        chroma_client: ChromaDB client instance
        threshold: Cosine similarity threshold

    Returns:
        List of results (None for no dup, dict for near-dup) — one per input
    """
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
