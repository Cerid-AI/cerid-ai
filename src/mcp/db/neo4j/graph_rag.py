# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Graph-based retrieval using Neo4j entity relationships.

Traverses the knowledge graph to find artifacts related to entities
extracted from user queries.  Designed as a complement to vector search
— results are scored by relationship proximity and node quality.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import config
from errors import RetrievalError

logger = logging.getLogger("ai-companion.graph_rag")


def _graph_retrieve_sync(
    driver: Any,
    entities: list[dict[str, str]],
    max_hops: int | None = None,
    max_results: int | None = None,
) -> list[dict[str, Any]]:
    """Find artifacts related to the given entities via graph traversal.

    Strategy:
    1. Find Neo4j nodes matching entity names (fuzzy match on title/content).
    2. Traverse relationships (RELATES_TO, REFERENCES, DEPENDS_ON) up to N hops.
    3. Score by: relationship proximity * node quality_score.
    4. Return ranked list of artifact metadata with graph scores.

    Args:
        driver: Neo4j driver instance.
        entities: List of {"text": str, "type": str} from entity extraction.
        max_hops: Maximum relationship hops (default from config).
        max_results: Maximum results to return (default from config).

    Returns:
        List of dicts with keys: artifact_id, filename, domain, graph_score,
        summary, quality_score, hop_distance.
    """
    if not driver or not entities:
        return []

    hops = min(max_hops or config.GRAPH_RAG_MAX_HOPS, 4)  # hard-clamp
    limit = min(max_results or config.GRAPH_RAG_MAX_RESULTS, 50)

    # Build entity name list for Cypher parameter binding
    entity_names = [e["text"] for e in entities if e.get("text")]
    if not entity_names:
        return []

    # Cypher: find artifacts whose filename, summary, or keywords contain
    # any of the entity names (case-insensitive substring match).
    # Then traverse outward up to `hops` hops and collect related artifacts.
    cypher = """
    UNWIND $entity_names AS entity_name
    MATCH (a:Artifact)
    WHERE toLower(a.filename) CONTAINS toLower(entity_name)
       OR toLower(a.summary) CONTAINS toLower(entity_name)
       OR toLower(a.keywords) CONTAINS toLower(entity_name)
    WITH COLLECT(DISTINCT a) AS seed_artifacts

    UNWIND seed_artifacts AS seed
    CALL {
        WITH seed
        MATCH path = (seed)-[*1..%(hops)d]-(related:Artifact)
        WHERE related <> seed
        WITH related, length(path) AS hop_distance
        RETURN related, MIN(hop_distance) AS min_hops
    }
    WITH related, min_hops
    RETURN DISTINCT
        related.id AS artifact_id,
        related.filename AS filename,
        related.domain AS domain,
        related.summary AS summary,
        related.quality_score AS quality_score,
        min_hops AS hop_distance
    ORDER BY related.quality_score DESC, min_hops ASC
    LIMIT $limit
    """ % {"hops": hops}

    results: list[dict[str, Any]] = []
    try:
        with driver.session() as session:
            records = session.run(
                cypher,
                entity_names=entity_names,
                limit=limit,
            )
            for record in records:
                quality = record.get("quality_score") or 0.5
                hop_dist = record.get("hop_distance") or 1
                # Score decay: quality * (1 / hop_distance)
                graph_score = round(quality * (1.0 / hop_dist), 4)

                results.append({
                    "artifact_id": record["artifact_id"],
                    "filename": record["filename"] or "",
                    "domain": record["domain"] or "",
                    "summary": record["summary"] or "",
                    "quality_score": quality,
                    "hop_distance": hop_dist,
                    "graph_score": graph_score,
                })
    except (RetrievalError, ValueError, OSError, RuntimeError) as exc:
        logger.warning("Graph RAG query failed: %s", exc)
        return []

    # Also include seed artifacts (direct entity matches) with top score
    seed_cypher = """
    UNWIND $entity_names AS entity_name
    MATCH (a:Artifact)
    WHERE toLower(a.filename) CONTAINS toLower(entity_name)
       OR toLower(a.summary) CONTAINS toLower(entity_name)
       OR toLower(a.keywords) CONTAINS toLower(entity_name)
    RETURN DISTINCT
        a.id AS artifact_id,
        a.filename AS filename,
        a.domain AS domain,
        a.summary AS summary,
        a.quality_score AS quality_score
    LIMIT $limit
    """
    try:
        seen_ids = {r["artifact_id"] for r in results}
        with driver.session() as session:
            seed_records = session.run(
                seed_cypher,
                entity_names=entity_names,
                limit=limit,
            )
            for record in seed_records:
                aid = record["artifact_id"]
                if aid in seen_ids:
                    continue
                seen_ids.add(aid)
                quality = record.get("quality_score") or 0.5
                results.append({
                    "artifact_id": aid,
                    "filename": record["filename"] or "",
                    "domain": record["domain"] or "",
                    "summary": record["summary"] or "",
                    "quality_score": quality,
                    "hop_distance": 0,
                    "graph_score": round(quality, 4),
                })
    except (RetrievalError, ValueError, OSError, RuntimeError) as exc:
        logger.warning("Graph RAG seed query failed: %s", exc)

    # Sort by graph_score descending
    results.sort(key=lambda x: x["graph_score"], reverse=True)
    return results[:limit]


async def graph_retrieve(
    driver: Any,
    entities: list[dict[str, str]],
    max_hops: int | None = None,
    max_results: int | None = None,
) -> list[dict[str, Any]]:
    """Async wrapper — runs sync Neo4j calls in a thread pool."""
    if not driver or not entities:
        return []

    from utils.circuit_breaker import get_breaker

    breaker = get_breaker("neo4j")
    try:
        return await breaker.call(
            asyncio.to_thread,
            _graph_retrieve_sync, driver, entities, max_hops, max_results,
        )
    except (RetrievalError, ValueError, OSError, RuntimeError) as exc:
        logger.warning("Graph RAG retrieval failed (circuit breaker): %s", exc)
        return []
