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
from typing import Any, Iterable

import config
from app.db.neo4j.facts import reconcile_fact_subjects
from core.agents.entity_extraction import Entity
from core.utils.swallowed import log_swallowed_error
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


# ===========================================================================
# Phase 4.2 — entity MERGE / UNMERGE machinery (provenance + reversible)
# ===========================================================================
#
# The Phase-4 audit's entity-resolution story: SIMILAR_TO kNN stays as
# *connectivity*; real *resolution* (collapsing two Entity nodes into one)
# happens here. Two callers use this:
#   - the embedding-resolution flow in scripts/merge_entity_aliases.py
#     (auto-merge band + LLM-adjudicated band), and
#   - any future admin/unmerge surface.
#
# Every merge is REVERSIBLE: a ``(:MergedEntity)`` tombstone records the
# merged-away node's identity + its source MENTIONS edges, plus a
# ``-[:MERGED_INTO]->`` edge to the survivor carrying merge provenance
# (merged_at / merge_confidence / merge_method). ``unmerge_entity`` replays
# the tombstone to restore the entity's identity + MENTIONS; derived edges
# (CO_MENTIONED / SIMILAR_TO / IN_COMMUNITY) regenerate on the next nightly
# sweep, so they are deliberately NOT snapshotted (they are re-derivable and
# would otherwise bloat the tombstone for the very >10k-edge entities the
# chunking exists to protect).
#
# Every edge re-point is CHUNKED (``config.ENTITY_MERGE_UNWIND_CHUNK``): a
# single-statement combined fetch+repoint+delete bounded by ``WITH ... LIMIT
# $limit``, looped until drained. This fixes the >10k UNWIND blow-up an
# unbounded UNWIND would hit on a heavily-connected entity.

# Provenance graph labels/edges (literals — never parameterised into Cypher).
_TOMBSTONE_LABEL = "MergedEntity"
_MERGED_INTO_EDGE = "MERGED_INTO"

# Merge-method tags recorded in provenance so a reversal audit can tell WHY a
# pair was merged (deterministic alias vs embedding vs LLM adjudication).
MERGE_METHOD_STRUCTURAL = "structural_alias"
MERGE_METHOD_EMBEDDING_AUTO = "embedding_auto"
MERGE_METHOD_EMBEDDING_ADJUDICATED = "embedding_adjudicated"


def _merge_chunk_size(chunk_size: int | None) -> int:
    """Resolve the UNWIND chunk size (arg override → settings → floor 1)."""
    if chunk_size is not None:
        return max(1, int(chunk_size))
    configured = int(getattr(config, "ENTITY_MERGE_UNWIND_CHUNK", 5000))
    return max(1, configured)


# ---------------------------------------------------------------------------
# Edge re-point Cypher.
#
# Each statement fetches AT MOST ``$limit`` of the loser's edges of one type,
# re-points them onto the survivor with type-correct dedup, deletes the old
# edge, and RETURNs how many it processed. Callers loop until 0. Relationship
# TYPES are literals (Cypher can't parameterise them); node identity flows via
# ``$loser_id`` / ``$survivor_id`` params — no injection surface.
# ---------------------------------------------------------------------------

# MENTIONS: (:Artifact)-[:MENTIONS]->(loser). Preserve confidence/chunk_ids/
# created_at on first re-point; MERGE dedups when the artifact already mentions
# the survivor.
_REPOINT_MENTIONS = """
MATCH (a:Artifact)-[m_old:MENTIONS]->(:Entity {canonical_id: $loser_id})
WITH a, m_old LIMIT $limit
MATCH (survivor:Entity {canonical_id: $survivor_id})
MERGE (a)-[m_new:MENTIONS]->(survivor)
  ON CREATE SET m_new.confidence = m_old.confidence,
                m_new.chunk_ids  = m_old.chunk_ids,
                m_new.created_at = m_old.created_at
DELETE m_old
RETURN count(*) AS processed
"""

# CO_MENTIONED (outbound): sum weight; drop the loser→survivor self edge.
_REPOINT_CO_MENTIONED_OUT = """
MATCH (:Entity {canonical_id: $loser_id})-[r_old:CO_MENTIONED]->(other:Entity)
WITH r_old, other LIMIT $limit
MATCH (survivor:Entity {canonical_id: $survivor_id})
FOREACH (_ IN CASE WHEN other.canonical_id = $survivor_id THEN [] ELSE [1] END |
    MERGE (survivor)-[r_new:CO_MENTIONED]->(other)
      ON CREATE SET r_new.weight = r_old.weight
      ON MATCH  SET r_new.weight = coalesce(r_new.weight, 0) + coalesce(r_old.weight, 0)
)
DELETE r_old
RETURN count(*) AS processed
"""

# CO_MENTIONED (inbound): mirror of the outbound case.
_REPOINT_CO_MENTIONED_IN = """
MATCH (other:Entity)-[r_old:CO_MENTIONED]->(:Entity {canonical_id: $loser_id})
WITH r_old, other LIMIT $limit
MATCH (survivor:Entity {canonical_id: $survivor_id})
FOREACH (_ IN CASE WHEN other.canonical_id = $survivor_id THEN [] ELSE [1] END |
    MERGE (other)-[r_new:CO_MENTIONED]->(survivor)
      ON CREATE SET r_new.weight = r_old.weight
      ON MATCH  SET r_new.weight = coalesce(r_new.weight, 0) + coalesce(r_old.weight, 0)
)
DELETE r_old
RETURN count(*) AS processed
"""

# SIMILAR_TO (outbound): keep the MAX score; drop the self edge. SIMILAR_TO is
# connectivity that the nightly job rebuilds, but re-pointing (rather than
# dropping) keeps the graph coherent between merge and the next sweep.
_REPOINT_SIMILAR_TO_OUT = """
MATCH (:Entity {canonical_id: $loser_id})-[r_old:SIMILAR_TO]->(other:Entity)
WITH r_old, other LIMIT $limit
MATCH (survivor:Entity {canonical_id: $survivor_id})
FOREACH (_ IN CASE WHEN other.canonical_id = $survivor_id THEN [] ELSE [1] END |
    MERGE (survivor)-[r_new:SIMILAR_TO]->(other)
      ON CREATE SET r_new.score = r_old.score
      ON MATCH  SET r_new.score = CASE
          WHEN coalesce(r_old.score, 0) > coalesce(r_new.score, 0)
          THEN r_old.score ELSE r_new.score END
)
DELETE r_old
RETURN count(*) AS processed
"""

# SIMILAR_TO (inbound): mirror of the outbound case.
_REPOINT_SIMILAR_TO_IN = """
MATCH (other:Entity)-[r_old:SIMILAR_TO]->(:Entity {canonical_id: $loser_id})
WITH r_old, other LIMIT $limit
MATCH (survivor:Entity {canonical_id: $survivor_id})
FOREACH (_ IN CASE WHEN other.canonical_id = $survivor_id THEN [] ELSE [1] END |
    MERGE (other)-[r_new:SIMILAR_TO]->(survivor)
      ON CREATE SET r_new.score = r_old.score
      ON MATCH  SET r_new.score = CASE
          WHEN coalesce(r_old.score, 0) > coalesce(r_new.score, 0)
          THEN r_old.score ELSE r_new.score END
)
DELETE r_old
RETURN count(*) AS processed
"""

# IN_COMMUNITY: (loser)-[:IN_COMMUNITY]->(:Community). MERGE dedups.
_REPOINT_IN_COMMUNITY = """
MATCH (:Entity {canonical_id: $loser_id})-[r_old:IN_COMMUNITY]->(c)
WITH r_old, c LIMIT $limit
MATCH (survivor:Entity {canonical_id: $survivor_id})
MERGE (survivor)-[:IN_COMMUNITY]->(c)
DELETE r_old
RETURN count(*) AS processed
"""

# HAS_CONTRADICTION: (loser)-[:HAS_CONTRADICTION]->(:ContradictionFinding).
_REPOINT_HAS_CONTRADICTION = """
MATCH (:Entity {canonical_id: $loser_id})-[r_old:HAS_CONTRADICTION]->(cf)
WITH r_old, cf LIMIT $limit
MATCH (survivor:Entity {canonical_id: $survivor_id})
MERGE (survivor)-[r_new:HAS_CONTRADICTION]->(cf)
  ON CREATE SET r_new.linked_at = r_old.linked_at
DELETE r_old
RETURN count(*) AS processed
"""

# ENRICHED_FROM: (loser)-[:ENRICHED_FROM {source}]->(:ExternalReference).
# Dedup on the (reference, source) pair the wiki layer keys on.
_REPOINT_ENRICHED_FROM = """
MATCH (:Entity {canonical_id: $loser_id})-[r_old:ENRICHED_FROM]->(ref)
WITH r_old, ref LIMIT $limit
MATCH (survivor:Entity {canonical_id: $survivor_id})
MERGE (survivor)-[r_new:ENRICHED_FROM {source: r_old.source}]->(ref)
  ON CREATE SET r_new.fetched_at = r_old.fetched_at
DELETE r_old
RETURN count(*) AS processed
"""

# HAS_FACT: (loser)-[:HAS_FACT]->(:Fact). Edge-only re-point — the :Fact node's
# denormalised subject_id/uid are fixed up separately by
# reconcile_fact_subjects (app/db/neo4j/facts.py), called from
# merge_entities right after this re-point loop drains for each loser.
_REPOINT_HAS_FACT = """
MATCH (:Entity {canonical_id: $loser_id})-[r_old:HAS_FACT]->(f)
WITH r_old, f LIMIT $limit
MATCH (survivor:Entity {canonical_id: $survivor_id})
MERGE (survivor)-[:HAS_FACT]->(f)
DELETE r_old
RETURN count(*) AS processed
"""

# FACT_OBJECT: (:Fact)-[:FACT_OBJECT]->(loser). Edge-only re-point (as above)
# — object_id property fix-up is also reconcile_fact_subjects's job.
_REPOINT_FACT_OBJECT = """
MATCH (f)-[r_old:FACT_OBJECT]->(:Entity {canonical_id: $loser_id})
WITH r_old, f LIMIT $limit
MATCH (survivor:Entity {canonical_id: $survivor_id})
MERGE (f)-[:FACT_OBJECT]->(survivor)
DELETE r_old
RETURN count(*) AS processed
"""

# The exhaustive re-point inventory. Order is irrelevant (each drains fully
# before the next). Enumerated from GRAPH_RELATIONSHIP_TYPES + the live schema
# (MENTIONS/CO_MENTIONED/SIMILAR_TO/IN_COMMUNITY/HAS_CONTRADICTION/
# ENRICHED_FROM/HAS_FACT/FACT_OBJECT). Any edge type NOT listed here is caught
# by the leftover-edge guard in ``_detach_delete_loser`` before deletion.
_ENTITY_EDGE_REPOINTS: tuple[tuple[str, str], ...] = (
    ("MENTIONS", _REPOINT_MENTIONS),
    ("CO_MENTIONED_OUT", _REPOINT_CO_MENTIONED_OUT),
    ("CO_MENTIONED_IN", _REPOINT_CO_MENTIONED_IN),
    ("SIMILAR_TO_OUT", _REPOINT_SIMILAR_TO_OUT),
    ("SIMILAR_TO_IN", _REPOINT_SIMILAR_TO_IN),
    ("IN_COMMUNITY", _REPOINT_IN_COMMUNITY),
    ("HAS_CONTRADICTION", _REPOINT_HAS_CONTRADICTION),
    ("ENRICHED_FROM", _REPOINT_ENRICHED_FROM),
    ("HAS_FACT", _REPOINT_HAS_FACT),
    ("FACT_OBJECT", _REPOINT_FACT_OBJECT),
)


def _run_chunked_repoint(
    session: Any,
    cypher: str,
    *,
    loser_id: str,
    survivor_id: str,
    chunk: int,
) -> int:
    """Loop one re-point statement until it drains; return total re-pointed."""
    total = 0
    while True:
        result = session.run(
            cypher, loser_id=loser_id, survivor_id=survivor_id, limit=chunk
        )
        row = result.single()
        processed = int(row["processed"]) if row and row["processed"] is not None else 0
        total += processed
        if processed == 0:
            break
    return total


_SNAPSHOT_MENTIONS = """
MATCH (a:Artifact)-[m:MENTIONS]->(:Entity {canonical_id: $loser_id})
RETURN a.id AS art_id,
       m.confidence AS confidence,
       m.chunk_ids  AS chunk_ids,
       m.created_at AS created_at
ORDER BY a.id
SKIP $skip LIMIT $limit
"""


def _snapshot_mentions(session: Any, loser_id: str, chunk: int) -> list[dict[str, Any]]:
    """Read the loser's MENTIONS edges (paginated) for reversibility."""
    snapshot: list[dict[str, Any]] = []
    skip = 0
    while True:
        rows = session.run(
            _SNAPSHOT_MENTIONS, loser_id=loser_id, skip=skip, limit=chunk
        ).data()
        if not rows:
            break
        snapshot.extend(rows)
        skip += len(rows)
        if len(rows) < chunk:
            break
    return snapshot


_READ_ENTITY = """
MATCH (e:Entity {canonical_id: $canonical_id})
RETURN e.name AS name,
       e.entity_type AS entity_type,
       coalesce(e.mention_count, 0) AS mention_count
"""

_LEFTOVER_EDGES = """
MATCH (e:Entity {canonical_id: $loser_id})-[r]-()
RETURN type(r) AS rel_type, count(*) AS cnt
"""


def _detach_delete_loser(session: Any, loser_id: str) -> None:
    """Delete the loser node, surfacing any un-repointed edge as a warning.

    The typed re-points above are exhaustive over today's schema; this guard
    exists so a NEW, un-enumerated entity-adjacent edge type surfaces in logs
    instead of being silently dropped by ``DETACH DELETE``.
    """
    leftovers = session.run(_LEFTOVER_EDGES, loser_id=loser_id).data()
    for row in leftovers:
        logger.warning(
            "entity_merge: loser=%s has %d un-repointed %s edge(s) — "
            "DETACH DELETE will drop them (add to _ENTITY_EDGE_REPOINTS)",
            loser_id, int(row["cnt"]), row["rel_type"],
        )
    session.run(
        "MATCH (loser:Entity {canonical_id: $loser_id}) DETACH DELETE loser",
        loser_id=loser_id,
    )


_UPSERT_SURVIVOR = """
MERGE (e:Entity {canonical_id: $canonical_id})
ON CREATE SET
    e.name          = $name,
    e.entity_type   = $entity_type,
    e.mention_count = 0,
    e.created_at    = $now
ON MATCH SET
    e.name       = CASE WHEN size($name) > size(coalesce(e.name, ''))
                        THEN $name ELSE e.name END,
    e.updated_at = $now
"""

_ADD_MENTION_COUNT = """
MATCH (survivor:Entity {canonical_id: $survivor_id})
SET survivor.mention_count = coalesce(survivor.mention_count, 0) + $delta
"""

_WRITE_TOMBSTONE = """
MATCH (survivor:Entity {canonical_id: $survivor_id})
MERGE (t:MergedEntity {canonical_id: $loser_id})
SET t.name             = $name,
    t.entity_type      = $entity_type,
    t.mention_count    = $mention_count,
    t.merged_at        = $merged_at,
    t.merge_confidence = $merge_confidence,
    t.merge_method     = $merge_method,
    t.survivor_id      = $survivor_id,
    t.mentions_snapshot = $mentions_snapshot
MERGE (t)-[rel:MERGED_INTO]->(survivor)
SET rel.merged_at        = $merged_at,
    rel.merge_confidence = $merge_confidence,
    rel.merge_method     = $merge_method
"""


def merge_entities(
    driver,
    survivor_id: str,
    loser_ids: list[str],
    *,
    survivor_name: str | None = None,
    entity_type: str | None = None,
    merge_method: str = MERGE_METHOD_STRUCTURAL,
    merge_confidence: float | None = None,
    chunk_size: int | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Collapse ``loser_ids`` into ``survivor_id`` — reversible, chunked.

    For each loser: snapshot its identity + MENTIONS onto a ``(:MergedEntity)``
    tombstone with a ``-[:MERGED_INTO]->`` provenance edge, re-point EVERY
    entity-adjacent edge type onto the survivor (chunked at
    ``config.ENTITY_MERGE_UNWIND_CHUNK``), reconcile the loser's :Fact
    subject/object identity onto the survivor
    (:func:`app.db.neo4j.facts.reconcile_fact_subjects` — the HAS_FACT/
    FACT_OBJECT edge re-point above moves pointers only; this fixes the
    :Fact node's own denormalised properties), fold its ``mention_count``
    into the survivor, then ``DETACH DELETE`` it.

    ``merge_method`` / ``merge_confidence`` are recorded on the tombstone +
    edge so a bad merge is auditable and reversible via
    :func:`unmerge_entity` — EXCEPT the fact reconciliation, which is
    one-way (see :func:`unmerge_entity`'s docstring).

    Returns a summary dict (survivor_id, merged loser ids, per-type re-point
    counts, skipped ids, ``fact_reconcile`` counts summed across losers).
    """
    chunk = _merge_chunk_size(chunk_size)
    ts = now or utcnow_iso()
    repoint_totals: dict[str, int] = {}
    fact_reconcile_totals: dict[str, int] = {}
    merged: list[str] = []
    skipped: list[str] = []

    with driver.session() as session:
        # Ensure the survivor exists (its canonical_id may differ from every
        # loser's — e.g. "elon-r-musk" normalises to an "elon-musk" that isn't
        # itself one of the losers).
        session.run(
            _UPSERT_SURVIVOR,
            canonical_id=survivor_id,
            name=survivor_name or survivor_id,
            entity_type=entity_type or "",
            now=ts,
        )

        for loser_id in loser_ids:
            if loser_id == survivor_id:
                skipped.append(loser_id)
                continue

            identity = session.run(_READ_ENTITY, canonical_id=loser_id).single()
            if identity is None:
                # Already gone (idempotent re-run) — nothing to do.
                skipped.append(loser_id)
                continue

            loser_mention_count = int(identity["mention_count"])
            mentions_snapshot = _snapshot_mentions(session, loser_id, chunk)

            for label, cypher in _ENTITY_EDGE_REPOINTS:
                count = _run_chunked_repoint(
                    session, cypher,
                    loser_id=loser_id, survivor_id=survivor_id, chunk=chunk,
                )
                repoint_totals[label] = repoint_totals.get(label, 0) + count

            fact_counts = reconcile_fact_subjects(
                session, survivor_id, [loser_id], chunk_size=chunk,
            )
            for key, value in fact_counts.items():
                fact_reconcile_totals[key] = fact_reconcile_totals.get(key, 0) + value

            session.run(
                _ADD_MENTION_COUNT,
                survivor_id=survivor_id,
                delta=loser_mention_count,
            )

            session.run(
                _WRITE_TOMBSTONE,
                survivor_id=survivor_id,
                loser_id=loser_id,
                name=identity["name"] or loser_id,
                entity_type=identity["entity_type"] or (entity_type or ""),
                mention_count=loser_mention_count,
                merged_at=ts,
                merge_confidence=(
                    float(merge_confidence) if merge_confidence is not None else None
                ),
                merge_method=merge_method,
                mentions_snapshot=json.dumps(mentions_snapshot),
            )

            _detach_delete_loser(session, loser_id)
            merged.append(loser_id)

    logger.info(
        "entity_merge survivor=%s merged=%d skipped=%d method=%s repoints=%s",
        survivor_id, len(merged), len(skipped), merge_method, repoint_totals,
    )
    return {
        "survivor_id": survivor_id,
        "merged": merged,
        "skipped": skipped,
        "repoint_totals": repoint_totals,
        "merge_method": merge_method,
        "fact_reconcile": fact_reconcile_totals,
    }


_READ_TOMBSTONE = """
MATCH (t:MergedEntity {canonical_id: $loser_id})-[:MERGED_INTO]->(survivor:Entity)
RETURN t.name AS name,
       t.entity_type AS entity_type,
       t.mention_count AS mention_count,
       t.mentions_snapshot AS mentions_snapshot,
       survivor.canonical_id AS survivor_id
"""

_RESTORE_ENTITY = """
MERGE (e:Entity {canonical_id: $canonical_id})
ON CREATE SET
    e.name          = $name,
    e.entity_type   = $entity_type,
    e.mention_count = $mention_count,
    e.created_at    = $now,
    e.updated_at    = $now
ON MATCH SET
    e.mention_count = $mention_count,
    e.updated_at    = $now
"""

_RESTORE_MENTIONS = """
MATCH (loser:Entity {canonical_id: $loser_id})
UNWIND $batch AS b
MATCH (a:Artifact {id: b.art_id})
MERGE (a)-[m:MENTIONS]->(loser)
ON CREATE SET m.confidence = b.confidence,
              m.chunk_ids  = b.chunk_ids,
              m.created_at  = b.created_at
"""

_DECREMENT_SURVIVOR = """
MATCH (survivor:Entity {canonical_id: $survivor_id})
SET survivor.mention_count =
    CASE WHEN coalesce(survivor.mention_count, 0) - $delta < 0
         THEN 0 ELSE coalesce(survivor.mention_count, 0) - $delta END
"""

_DELETE_TOMBSTONE = """
MATCH (t:MergedEntity {canonical_id: $loser_id})
DETACH DELETE t
"""

# Operator signal only (see unmerge_entity's docstring on why fact
# reconciliation is one-way): a non-zero count means the restored loser will
# NOT regain any fact attribution the merge folded onto the survivor.
_SURVIVOR_HAS_FACTS = """
MATCH (survivor:Entity {canonical_id: $survivor_id})-[:HAS_FACT]->(:Fact)
RETURN count(*) AS fact_count
"""


def unmerge_entity(
    driver,
    loser_id: str,
    *,
    chunk_size: int | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Reverse a merge: restore ``loser_id``'s identity + MENTIONS from its
    tombstone, decrement the survivor's ``mention_count``, drop the tombstone.

    Derived edges (CO_MENTIONED / SIMILAR_TO / IN_COMMUNITY) are NOT restored
    here — they regenerate on the next nightly co-mention / semantic-edge /
    community sweep from the restored MENTIONS.

    :Fact subject/object reconciliation is likewise NOT reversed:
    ``reconcile_fact_subjects`` rewrote the fact's ``uid`` in place (and may
    have folded a colliding loser fact into the survivor's, deleting the
    loser's fact node outright) during the merge — there is no snapshot to
    replay a fact identity back onto the restored loser. A ``logger.warning``
    fires when the survivor still carries any ``:Fact`` at unmerge time as an
    operator signal; re-run extraction on the restored entity if fact
    accuracy matters.

    Returns a summary dict; ``{"status": "no_provenance"}`` when no tombstone
    exists for ``loser_id``.
    """
    chunk = _merge_chunk_size(chunk_size)
    ts = now or utcnow_iso()

    with driver.session() as session:
        tomb = session.run(_READ_TOMBSTONE, loser_id=loser_id).single()
        if tomb is None:
            logger.warning("entity_unmerge: no tombstone for %s", loser_id)
            return {"status": "no_provenance", "loser_id": loser_id}

        survivor_id = tomb["survivor_id"]
        mention_count = int(tomb["mention_count"] or 0)
        try:
            snapshot = json.loads(tomb["mentions_snapshot"] or "[]")
        except (json.JSONDecodeError, TypeError) as exc:
            log_swallowed_error("app.db.neo4j.entity.unmerge_entity.snapshot", exc)
            snapshot = []

        fact_row = session.run(_SURVIVOR_HAS_FACTS, survivor_id=survivor_id).single()
        survivor_fact_count = int(fact_row["fact_count"]) if fact_row else 0
        if survivor_fact_count:
            logger.warning(
                "entity_unmerge: survivor=%s carries %d :Fact node(s) — fact "
                "subject/object attribution from the merge is one-way "
                "(reconcile_fact_subjects rewrote uid in place; the tombstone "
                "snapshot carries no fact state) — restored loser=%s will NOT "
                "regain any fact attribution; re-run extraction on it if fact "
                "accuracy matters",
                survivor_id, survivor_fact_count, loser_id,
            )

        session.run(
            _RESTORE_ENTITY,
            canonical_id=loser_id,
            name=tomb["name"] or loser_id,
            entity_type=tomb["entity_type"] or "",
            mention_count=mention_count,
            now=ts,
        )

        restored_mentions = 0
        for start in range(0, len(snapshot), chunk):
            batch = snapshot[start : start + chunk]
            session.run(_RESTORE_MENTIONS, loser_id=loser_id, batch=batch)
            restored_mentions += len(batch)

        session.run(_DECREMENT_SURVIVOR, survivor_id=survivor_id, delta=mention_count)
        session.run(_DELETE_TOMBSTONE, loser_id=loser_id)

    logger.info(
        "entity_unmerge loser=%s survivor=%s restored_mentions=%d",
        loser_id, survivor_id, restored_mentions,
    )
    return {
        "status": "restored",
        "loser_id": loser_id,
        "survivor_id": survivor_id,
        "restored_mentions": restored_mentions,
    }
