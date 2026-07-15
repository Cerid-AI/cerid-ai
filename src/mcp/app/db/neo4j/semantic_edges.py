# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Materialise ``(:Entity)-[:SIMILAR_TO {score}]->(:Entity)`` semantic kNN edges.

Workstream E Phase 3.2.  Reads the per-entity ``embedding`` property written
by ``compute_entity_embeddings`` (JSON-encoded L2-normalised float32 list),
performs a brute-force cosine kNN via chunked matrix multiply (≤50k entities
fit comfortably in RAM), and writes directed ``SIMILAR_TO`` edges for every
qualifying (entity, neighbour) pair.

**Idempotent drop+rebuild** mirrors ``community_detection.py``::

    MATCH ()-[r:SIMILAR_TO]-() DELETE r   # purge previous run
    ... MERGE (a)-[r:SIMILAR_TO]->(b) SET r.score = <cosine>

**Edge direction** mirrors ``CO_MENTIONED``: ``WHERE id(e1) < id(e2)`` ensures
one canonical directed edge per undirected pair (lower internal Neo4j id →
higher).  Because we only have ``canonical_id`` strings (not Neo4j internal
ids) in the Python layer, we use lexicographic order on ``canonical_id`` as
the tiebreaker — stable across runs.

**CO_MENTIONED exclusion**: pairs already connected by ``CO_MENTIONED`` are
loaded upfront and skipped — they already carry structural co-occurrence
semantics; adding a ``SIMILAR_TO`` edge would be redundant.

**Disabled gate**: when ``config.SEMANTIC_EDGE_ENABLED`` is ``False``, the
function is a no-op returning ``{"skipped": "disabled"}``.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import config
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.semantic_edges")

# Chunked matrix-multiply batch size — matches compute_umap_3d.py pattern.
_CHUNK = 512
# Chunked UNWIND write batch size. A full-graph run can emit ~250k SIMILAR_TO
# edges (k=5); writing them in a single UNWIND transaction risks OOM, so the
# MERGE is paginated at this size.
_WRITE_CHUNK = 10000


def build_similarity_edges(
    driver: Any,
    *,
    k: int = 5,
    threshold: float = 0.82,
    neo4j_database: str | None = None,
) -> dict[str, Any]:
    """Materialise ``SIMILAR_TO`` kNN edges in Neo4j.

    Parameters
    ----------
    driver:
        Neo4j driver instance.
    k:
        Number of nearest neighbours per entity to consider.
    threshold:
        Minimum cosine similarity required for an edge.  Vectors are
        L2-normalised, so cosine = dot product.
    neo4j_database:
        Optional database name (passed to ``driver.session``).

    Returns
    -------
    dict with keys ``edges_created``, ``entities_with_embeddings``,
    ``elapsed_seconds`` — or ``{"skipped": "disabled"}`` when the feature
    flag is off.
    """
    if not config.SEMANTIC_EDGE_ENABLED:
        return {"skipped": "disabled"}

    import numpy as np  # noqa: PLC0415 — heavy import deferred to call time

    started = time.time()
    session_kw: dict[str, Any] = {}
    if neo4j_database is not None:
        session_kw["database"] = neo4j_database

    # ------------------------------------------------------------------
    # 1. Idempotent purge of previous SIMILAR_TO edges.
    # ------------------------------------------------------------------
    with driver.session(**session_kw) as session:
        session.run("MATCH ()-[r:SIMILAR_TO]-() DELETE r")

    # ------------------------------------------------------------------
    # 2. Fetch entities that have embeddings.
    # ------------------------------------------------------------------
    with driver.session(**session_kw) as session:
        rows = list(session.run(
            """
            MATCH (e:Entity)
            WHERE e.embedding IS NOT NULL AND e.canonical_id IS NOT NULL
            RETURN e.canonical_id AS canonical_id,
                   e.embedding    AS embedding,
                   coalesce(e.co_mention_degree, 0) AS co_mention_degree
            """
        ))

    if not rows:
        elapsed = round(time.time() - started, 2)
        logger.info("semantic_edges: no entities with embeddings, elapsed=%.2fs", elapsed)
        return {"edges_created": 0, "entities_with_embeddings": 0, "elapsed_seconds": elapsed}

    # ------------------------------------------------------------------
    # 3. Parse embeddings into a float32 matrix.
    # ------------------------------------------------------------------
    canonical_ids: list[str] = []
    raw_vecs: list[Any] = []

    for row in rows:
        try:
            vec = np.asarray(json.loads(row["embedding"]), dtype=np.float32)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            log_swallowed_error(
                "semantic_edges._parse_embedding",
                exc,
                context={"canonical_id": row["canonical_id"]},
            )
            continue
        canonical_ids.append(row["canonical_id"])
        raw_vecs.append(vec)

    if not canonical_ids:
        elapsed = round(time.time() - started, 2)
        return {"edges_created": 0, "entities_with_embeddings": 0, "elapsed_seconds": elapsed}

    n = len(canonical_ids)
    emb_matrix = np.stack(raw_vecs, axis=0)  # (n, d) float32

    # ------------------------------------------------------------------
    # 4. Load CO_MENTIONED pairs to exclude.
    # ------------------------------------------------------------------
    co_mentioned_set: set[tuple[str, str]] = set()
    with driver.session(**session_kw) as session:
        cm_rows = list(session.run(
            """
            MATCH (a:Entity)-[:CO_MENTIONED]-(b:Entity)
            WHERE a.canonical_id IS NOT NULL AND b.canonical_id IS NOT NULL
            RETURN a.canonical_id AS a_id, b.canonical_id AS b_id
            """
        ))
    for cm_row in cm_rows:
        a_id = cm_row["a_id"]
        b_id = cm_row["b_id"]
        # Normalise to (lower, higher) so lookup is order-independent.
        pair = (min(a_id, b_id), max(a_id, b_id))
        co_mentioned_set.add(pair)

    # ------------------------------------------------------------------
    # 5. Chunked cosine kNN (dot product — vectors already L2-normalised).
    #    Canonical direction: lower canonical_id → higher canonical_id.
    # ------------------------------------------------------------------
    # Build a sorted index mapping canonical_id → position, so we can
    # assign a stable canonical direction without Neo4j internal ids.
    id_rank: dict[str, int] = {cid: i for i, cid in enumerate(sorted(canonical_ids))}

    # Collect qualifying (from_id, to_id, score) — one per unordered pair.
    edge_seen: set[tuple[str, str]] = set()
    edge_rows: list[dict[str, Any]] = []

    for start in range(0, n, _CHUNK):
        end = min(start + _CHUNK, n)
        chunk_emb = emb_matrix[start:end]          # (c, d)
        # Full row of scores for this chunk vs all entities: (c, n)
        scores = chunk_emb @ emb_matrix.T          # (c, n)

        for local_i, global_i in enumerate(range(start, end)):
            cid_i = canonical_ids[global_i]
            score_row = scores[local_i]             # (n,)

            # Mask self.
            score_row[global_i] = -1.0

            # Top-k candidates above threshold.
            above = np.where(score_row >= threshold)[0]
            if above.size == 0:
                continue

            # Sort descending, take top k.
            top_k = above[np.argsort(score_row[above])[::-1][:k]]

            for global_j in top_k:
                cid_j = canonical_ids[int(global_j)]

                # Canonical pair direction: lower ranked id is the source.
                if id_rank[cid_i] < id_rank[cid_j]:
                    from_id, to_id = cid_i, cid_j
                else:
                    from_id, to_id = cid_j, cid_i

                # Deduplicate unordered pairs.
                pair_key = (from_id, to_id)
                if pair_key in edge_seen:
                    continue
                edge_seen.add(pair_key)

                # Skip CO_MENTIONED pairs.
                co_key = (min(from_id, to_id), max(from_id, to_id))
                if co_key in co_mentioned_set:
                    continue

                edge_rows.append({
                    "from_id": from_id,
                    "to_id": to_id,
                    "score": float(score_row[global_j]),
                })

    # ------------------------------------------------------------------
    # 6. MERGE SIMILAR_TO edges — chunked at _WRITE_CHUNK so a full-graph run
    #    (~250k edges at k=5) never rides in a single UNWIND transaction.
    # ------------------------------------------------------------------
    edges_created = 0
    if edge_rows:
        merge_cypher = """
            UNWIND $rows AS row
            MATCH (a:Entity {canonical_id: row.from_id})
            MATCH (b:Entity {canonical_id: row.to_id})
            MERGE (a)-[r:SIMILAR_TO]->(b)
            SET r.score = row.score
        """
        try:
            with driver.session(**session_kw) as session:
                for start in range(0, len(edge_rows), _WRITE_CHUNK):
                    batch = edge_rows[start : start + _WRITE_CHUNK]
                    result = session.run(merge_cypher, rows=batch)
                    summary = result.consume()
                    edges_created += summary.counters.relationships_created
        except Exception as exc:
            log_swallowed_error("app.db.neo4j.semantic_edges.merge", exc)
            raise

    elapsed = round(time.time() - started, 2)
    stats: dict[str, Any] = {
        "edges_created": edges_created,
        "entities_with_embeddings": n,
        "elapsed_seconds": elapsed,
    }
    logger.info("semantic_edges: %s", stats)
    return stats
