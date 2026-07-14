# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j persistence for the GraphRAG entity layer.

Workstream E Phase 4a.3 — write path. Idempotent UPSERT of
:class:`core.agents.entity_extraction.Entity` records as ``(:Entity)``
nodes plus ``(:Artifact)-[:MENTIONS]->(:Entity)`` edges.

Schema (declared in ``app/db/neo4j/schema.py::init_schema``):

    (:Entity {canonical_id, name, entity_type, created_at, updated_at})
    (:Artifact)-[:MENTIONS {confidence, chunk_ids, created_at}]->(:Entity)

The MENTIONS edge carries ``chunk_ids`` as a JSON-encoded list (matches
the ``keywords_json`` convention from ``CONVENTIONS.md`` § Data &
storage — Neo4j metadata is strings/ints only; lists ride as JSON
strings to keep Cypher's ``where`` operator set predictable).
"""
from __future__ import annotations

import json
import logging
from typing import Iterable

from core.agents.entity_extraction import Entity
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.graph.entity")


def upsert_entities_for_artifact(
    driver,
    artifact_id: str,
    entities: Iterable[Entity],
    chunk_ids: list[str] | None = None,
) -> dict:
    """Idempotent UPSERT of entities + MENTIONS edges for one artifact.

    Each :class:`Entity` produces or updates one ``(:Entity)`` node
    (keyed by ``canonical_id``) and one MENTIONS edge from the artifact.
    Re-running with the same arguments is safe: existing nodes have
    their ``updated_at`` refreshed and ``mention_count`` incremented;
    duplicate edges collapse via MERGE on the unique
    ``(artifact_id, canonical_id)`` pair.

    ``chunk_ids`` is the list of chunk IDs *within this artifact* that
    the entities were extracted from. Stored on every MENTIONS edge as
    a JSON-encoded string so per-chunk filters in
    ``ChromaNeo4jRetriever`` can intersect with the chroma side without
    requiring ``:Chunk`` nodes in Neo4j.

    Returns a dict with ``entities_upserted`` + ``edges_upserted`` for
    callers (the backfill script reports these in checkpoints).
    """
    ent_list = list(entities)
    stats = {"entities_upserted": 0, "edges_upserted": 0}
    if not ent_list:
        return stats

    now = utcnow_iso()
    chunk_ids_json = json.dumps(chunk_ids or [])

    payload = [
        {
            "canonical_id": e.canonical_id,
            "name": e.name,
            "entity_type": e.entity_type,
            "confidence": float(e.confidence),
        }
        for e in ent_list
    ]

    with driver.session() as session:
        result = session.run(
            """
            MATCH (a:Artifact {id: $artifact_id})
            UNWIND $payload AS p
            MERGE (e:Entity {canonical_id: p.canonical_id})
              ON CREATE SET
                e.name = p.name,
                e.entity_type = p.entity_type,
                e.created_at = $now,
                e.updated_at = $now,
                e.mention_count = 0
              ON MATCH SET
                e.updated_at = $now
            MERGE (a)-[m:MENTIONS]->(e)
              ON CREATE SET
                m.confidence = p.confidence,
                m.chunk_ids = $chunk_ids_json,
                m.created_at = $now,
                e.mention_count = coalesce(e.mention_count, 0) + 1
              ON MATCH SET
                m.confidence = (
                  CASE WHEN p.confidence > coalesce(m.confidence, 0)
                       THEN p.confidence
                       ELSE m.confidence END
                ),
                m.chunk_ids = $chunk_ids_json
            RETURN count(DISTINCT e) AS ents, count(m) AS edges
            """,
            artifact_id=artifact_id,
            payload=payload,
            chunk_ids_json=chunk_ids_json,
            now=now,
        )
        row = result.single()
        if row is not None:
            stats["entities_upserted"] = int(row["ents"])
            stats["edges_upserted"] = int(row["edges"])

    logger.debug(
        "entity_upsert artifact=%s entities=%d edges=%d",
        artifact_id, stats["entities_upserted"], stats["edges_upserted"],
    )
    return stats


def list_entities_for_artifact(driver, artifact_id: str) -> list[dict]:
    """Read-back helper for tests + introspection."""
    with driver.session() as session:
        rows = session.run(
            """
            MATCH (a:Artifact {id: $artifact_id})-[m:MENTIONS]->(e:Entity)
            RETURN e.canonical_id AS canonical_id,
                   e.name AS name,
                   e.entity_type AS entity_type,
                   m.confidence AS confidence,
                   m.chunk_ids AS chunk_ids_json
            ORDER BY e.canonical_id
            """,
            artifact_id=artifact_id,
        )
        return [dict(r) for r in rows]


def remove_mentions_for_artifact(driver, artifact_id: str) -> int:
    """Delete this artifact's ``MENTIONS`` edges ahead of re-extraction.

    Phase 4.3 (re-ingest hygiene). On content change, the artifact's
    existing MENTIONS edges were derived from the OLD content — the
    ``chunk_ids`` they carry may no longer exist once the re-ingest
    replaces the artifact's chunks — so they are stale and must not
    survive the re-ingest. This deletes ONLY the ``(:Artifact)-[:MENTIONS]
    ->(:Entity)`` edges owned by ``artifact_id``; it does not touch any
    other edge type (``RELATES_TO``, ``WIKILINKS_TO``, ``TAGGED_WITH``,
    ``BELONGS_TO``, ``HAS_ATTACHMENT``, ...) and never deletes the
    ``Entity`` node itself — those are the human-curated / cross-artifact
    graph relationships the re-ingest contract preserves.

    An ``Entity`` that loses its last MENTIONS edge here becomes an
    orphan (0 inbound MENTIONS). It is left in place: the existing
    nightly ``DeriveDomainsJob`` (``app/processor/jobs/derive_domains.py``)
    already detects entities with no MENTIONS path and clears their
    derived fields (``primary_domain`` etc.) rather than deleting the
    node, and a future artifact that mentions the same ``canonical_id``
    re-attaches to it via ``upsert_entities_for_artifact``'s MERGE.

    Returns the number of MENTIONS edges removed (for logging/tests).
    """
    with driver.session() as session:
        result = session.run(
            """
            MATCH (a:Artifact {id: $artifact_id})-[m:MENTIONS]->(:Entity)
            DELETE m
            RETURN count(m) AS removed
            """,
            artifact_id=artifact_id,
        )
        row = result.single()
        removed = int(row["removed"]) if row else 0
    logger.debug(
        "mentions_removed artifact=%s removed=%d", artifact_id, removed,
    )
    return removed
