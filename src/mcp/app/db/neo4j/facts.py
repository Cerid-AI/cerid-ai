# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j persistence for the bi-temporal :Fact layer (m0004/m0006).

Bi-temporal memory plan Phase C (C2) — the first (and only) writer of the
``:Fact`` nodes m0004 scaffolded. Idempotent MERGE of
:class:`core.agents.fact_derivation.DerivedFact` records:

    (:Entity)-[:HAS_FACT]->(:Fact)-[:FACT_OBJECT]->(:Entity)   // FACT_OBJECT binary-only
    (:Artifact)-[:FACT]->(:Fact)                               // provenance

Dedup identity is m0004's single-property Community-Edition key
``uid = "{subject_id}|{fact_key}"``: a MERGE on ``uid`` collapses a re-extracted
fact to one node, so ``count(DISTINCT f)`` is a symbolic (not LLM-trusted)
count. ``fact_key`` already encodes the EVENT-vs-STATE split (event_date in the
key for EVENT facts, absent for STATE) upstream in the derivation layer.

Bi-temporal stamps (the four-timestamp contract — see m0006 docstring):
``created_at`` = now (system time); ``invalid_at`` = null (active belief);
``valid_from`` = the memory's world-time start; ``valid_to`` = null (still true).
Interval closure (moving ``valid_to``/``invalid_at``) is Phase D — this writer
only opens intervals, and never re-opens one Phase-D closure already closed
(no silent belief flip-back; ``write_facts``'s ``facts_matched_closed`` count
is the telemetry signal for building re-assertion support later). Phase D
also reconciles a merged entity's :Fact subject/object identity
(``reconcile_fact_subjects``, called from
``app.db.neo4j.entity.merge_entities``) — the entity-merge edge re-point
moves HAS_FACT/FACT_OBJECT pointers only; this module's uid MERGE identity
is what the reconciler fixes up to match.

Orphan-safety (the zero-orphan :Fact health invariant,
``app/startup/invariants.py::_probe_fact_orphans``): the ``(:Fact)`` node and its
inbound ``(:Entity)-[:HAS_FACT]->`` edge are MERGEd in the SAME ``session.run``
(one transaction), so no :Fact is ever visible without an inbound HAS_FACT.

Writes are chunked (``FACT_WRITE_CHUNK_SIZE`` rows per UNWIND) so a large batch
cannot build one oversized transaction (the >10k-row lesson from Phase 4.2).
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import config
from core.agents.fact_derivation import DerivedFact, fact_uid
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.graph.facts")

# Max rows per UNWIND transaction. Bounds a single fact-write batch so it never
# builds one oversized transaction (the >10k-row memory/latency lesson Phase 4.2
# flags). Memory-derived batches are tiny (<= MAX_FACTS_PER_MEMORY) today; this
# guards the resumable backfill path that fans over the whole corpus later.
FACT_WRITE_CHUNK_SIZE = 1000

# Node + inbound HAS_FACT edge are MERGEd together so the zero-orphan invariant
# holds mid-write. Provenance (:Artifact)-[:FACT]-> uses OPTIONAL MATCH so a
# missing source artifact never aborts the row (the fact still gets its
# HAS_FACT edge — orphan-safe). FACT_OBJECT is written only for binary facts.
#
# ON CREATE SET only: a re-asserted fact whose uid matches a node Phase-D
# closure already CLOSED (invalid_at set) is NOT re-opened here — no silent
# belief flip-back (re-assertion support is deferred until a versioning
# design exists). The provenance edge still MERGEs for a closed match (the
# artifact genuinely references the subject; only the belief state is
# closed) — the RETURN splits facts_matched_closed out of facts_written so
# callers can see how often this happens.
_WRITE_FACTS_CYPHER = """
UNWIND $rows AS row
MERGE (subj:Entity {canonical_id: row.subject_id})
MERGE (f:Fact {uid: row.uid})
  ON CREATE SET
    f.subject_id = row.subject_id,
    f.object_id  = row.object_id,
    f.predicate  = row.predicate,
    f.fact_key   = row.fact_key,
    f.event_date = row.event_date,
    f.valid_from = row.valid_from,
    f.valid_to   = row.valid_to,
    f.invalid_at = row.invalid_at,
    f.created_at = row.created_at,
    f.source     = row.source
MERGE (subj)-[:HAS_FACT]->(f)
WITH f, row
OPTIONAL MATCH (a:Artifact {id: row.source_artifact_id})
FOREACH (_ IN CASE WHEN a IS NULL THEN [] ELSE [1] END |
  MERGE (a)-[:FACT]->(f)
)
FOREACH (oid IN CASE WHEN row.object_id IS NULL OR row.object_id = '' THEN [] ELSE [row.object_id] END |
  MERGE (obj:Entity {canonical_id: oid})
  MERGE (f)-[:FACT_OBJECT]->(obj)
)
RETURN count(DISTINCT f) AS facts_written,
       count(DISTINCT CASE WHEN f.invalid_at IS NOT NULL THEN f END) AS facts_matched_closed
"""


def _build_rows(
    facts: Sequence[DerivedFact], *, source_artifact_id: str, created_at: str
) -> list[dict]:
    """Materialise write rows, deduplicated by ``uid`` (identical facts collapse
    before the UNWIND — belt-and-braces with the DB-level MERGE dedup)."""
    by_uid: dict[str, dict] = {}
    for fact in facts:
        uid = fact_uid(fact.subject_id, fact.fact_key)
        # First writer wins per uid within a batch; the DB MERGE is idempotent
        # regardless, so this only trims the payload.
        by_uid.setdefault(
            uid,
            {
                "uid": uid,
                "subject_id": fact.subject_id,
                "object_id": fact.object_id,
                "predicate": fact.predicate,
                "fact_key": fact.fact_key,
                "event_date": fact.event_date,
                "valid_from": fact.valid_from,
                "valid_to": None,      # open interval (still true)
                "invalid_at": None,    # active belief
                "created_at": created_at,
                "source": fact.source,
                "source_artifact_id": source_artifact_id,
            },
        )
    return list(by_uid.values())


def write_facts(
    driver,
    facts: Sequence[DerivedFact],
    *,
    source_artifact_id: str,
    chunk_size: int = FACT_WRITE_CHUNK_SIZE,
) -> dict[str, int]:
    """MERGE ``facts`` as bi-temporal ``(:Fact)`` nodes for one source artifact.

    Idempotent: re-running with the same facts collapses to the same nodes via
    the ``uid`` MERGE and leaves any Phase-D interval closure untouched (only
    ``ON CREATE`` sets the stamps) — a closed fact stays closed; nothing here
    re-opens an interval Phase-D closure code set. Returns
    ``{"facts_written", "facts_matched_closed", "chunks"}``: ``facts_matched_
    closed`` counts the subset of touched facts that were already-closed
    nodes this batch matched (not created) — the telemetry signal for
    building re-assertion/belief-flip-back support later, not an error
    condition. Lets store exceptions propagate — the caller (the
    entity-extraction job's fact step) wraps this in ``log_swallowed_error`` so a
    fact-write failure never loses the already-successful entity extraction.
    """
    rows = _build_rows(
        facts, source_artifact_id=source_artifact_id, created_at=utcnow_iso()
    )
    if not rows:
        return {"facts_written": 0, "facts_matched_closed": 0, "chunks": 0}

    written = 0
    matched_closed = 0
    chunks = 0
    with driver.session() as session:
        for start in range(0, len(rows), chunk_size):
            batch = rows[start : start + chunk_size]
            row = session.run(_WRITE_FACTS_CYPHER, rows=batch).single()
            if row is not None:
                written += int(row["facts_written"])
                matched_closed += int(row["facts_matched_closed"])
            chunks += 1

    logger.debug(
        "facts_written artifact=%s facts=%d matched_closed=%d chunks=%d",
        source_artifact_id, written, matched_closed, chunks,
    )
    return {
        "facts_written": written,
        "facts_matched_closed": matched_closed,
        "chunks": chunks,
    }


# ===========================================================================
# Phase D — post-merge :Fact subject/object property reconciliation
# ===========================================================================
#
# app.db.neo4j.entity.merge_entities re-points the HAS_FACT/FACT_OBJECT EDGES
# onto the survivor (edge-only — see the comment there); it does not touch the
# :Fact node's own denormalised subject_id/object_id/uid properties, because
# that is this writer's dedup contract (m0004's uid = "{subject_id}|
# {fact_key}"). This is that contract's other half: called once per loser,
# right after its edge re-point loop drains, so a merged entity's facts land
# back under the SAME uid MERGE identity write_facts uses going forward.


def _reconcile_chunk_size(chunk_size: int | None) -> int:
    """Resolve the UNWIND-loop chunk size (arg override -> settings -> floor
    1) — mirrors entity.py's ``_merge_chunk_size`` (same
    ``config.ENTITY_MERGE_UNWIND_CHUNK`` knob; merge_entities passes its
    already-resolved value through so both stay in lockstep for one merge)."""
    if chunk_size is not None:
        return max(1, int(chunk_size))
    configured = int(getattr(config, "ENTITY_MERGE_UNWIND_CHUNK", 5000))
    return max(1, configured)


# Subject re-point, no collision: rewrite f's own uid/subject_id in place.
# The OPTIONAL MATCH + WHERE g IS NULL filter is evaluated per-row BEFORE the
# LIMIT, so a row only reaches the SET once its target uid is confirmed free.
_RECONCILE_SUBJECT_NO_COLLISION = """
MATCH (f:Fact) WHERE f.subject_id IN $loser_ids
WITH f, $survivor_id + '|' + f.fact_key AS new_uid
OPTIONAL MATCH (g:Fact {uid: new_uid})
WITH f, new_uid WHERE g IS NULL
WITH f, new_uid LIMIT $limit
SET f.subject_id = $survivor_id, f.uid = new_uid
RETURN count(*) AS processed
"""

# Subject re-point, collision: a survivor fact `g` already asserts the
# identical fact_key — pre-merge duplication, not a belief conflict. Fold f's
# interval (open beats closed) and source (verification wins, Risk R5) onto
# g, re-point f's provenance/FACT_OBJECT edges onto g, then DETACH DELETE f —
# dedup collapse of the SAME fact identity (the same class of operation as
# _detach_delete_loser collapsing the same entity identity), never a belief
# revision.
_RECONCILE_SUBJECT_COLLISION_FOLD = """
MATCH (f:Fact) WHERE f.subject_id IN $loser_ids
WITH f, $survivor_id + '|' + f.fact_key AS new_uid
MATCH (g:Fact {uid: new_uid})
WITH f, g LIMIT $limit
SET g.valid_from = CASE
      WHEN f.valid_from IS NULL OR f.valid_from = '' THEN g.valid_from
      WHEN g.valid_from IS NULL OR g.valid_from = '' THEN f.valid_from
      WHEN f.valid_from < g.valid_from THEN f.valid_from
      ELSE g.valid_from
    END,
    g.valid_to = CASE
      WHEN f.valid_to IS NULL OR g.valid_to IS NULL THEN NULL
      WHEN f.valid_to > g.valid_to THEN f.valid_to
      ELSE g.valid_to
    END,
    g.invalid_at = CASE
      WHEN f.invalid_at IS NULL OR g.invalid_at IS NULL THEN NULL
      WHEN f.invalid_at > g.invalid_at THEN f.invalid_at
      ELSE g.invalid_at
    END,
    g.source = CASE
      WHEN f.source = 'verification' OR g.source = 'verification' THEN 'verification'
      ELSE g.source
    END
WITH f, g
OPTIONAL MATCH (a:Artifact)-[:FACT]->(f)
WITH f, g, collect(DISTINCT a) AS provenance_artifacts
OPTIONAL MATCH (f)-[:FACT_OBJECT]->(obj:Entity)
WITH f, g, provenance_artifacts, collect(DISTINCT obj) AS fact_objects
FOREACH (a IN provenance_artifacts | MERGE (a)-[:FACT]->(g))
FOREACH (obj IN fact_objects | MERGE (g)-[:FACT_OBJECT]->(obj))
DETACH DELETE f
RETURN count(*) AS processed
"""

# Object re-point: property-only. Phase C writes unary facts (object_id
# always NULL) so a :Fact actually matching object_id IN $loser_ids is
# necessarily a hypothetical binary fact — fact_key's "|"-joined segments are
# positionally ambiguous to rewrite safely (predicate|object_id|event_date vs
# predicate|event_date can't be told apart from the string alone), so uid/
# fact_key are deliberately left untouched; the caller logs a warning when
# this fires at all (see reconcile_fact_subjects).
_RECONCILE_OBJECT_ID = """
MATCH (f:Fact) WHERE f.object_id IN $loser_ids
WITH f LIMIT $limit
SET f.object_id = $survivor_id
RETURN count(*) AS processed
"""


def _run_chunked_reconcile(
    session: Any, cypher: str, *, survivor_id: str, loser_ids: list[str], chunk: int
) -> int:
    """Loop one reconciliation statement until it drains; return rows
    processed. Idempotent by construction: each statement's own MATCH no
    longer selects a row once it's been repointed/folded, so a re-run drains
    immediately with 0 processed."""
    total = 0
    while True:
        result = session.run(
            cypher, survivor_id=survivor_id, loser_ids=loser_ids, limit=chunk
        )
        row = result.single()
        processed = int(row["processed"]) if row and row["processed"] is not None else 0
        total += processed
        if processed == 0:
            break
    return total


def _run_fact_reconcile(
    session: Any, survivor_id: str, loser_ids: list[str], chunk: int
) -> dict[str, int]:
    subjects_repointed = _run_chunked_reconcile(
        session, _RECONCILE_SUBJECT_NO_COLLISION,
        survivor_id=survivor_id, loser_ids=loser_ids, chunk=chunk,
    )
    facts_folded = _run_chunked_reconcile(
        session, _RECONCILE_SUBJECT_COLLISION_FOLD,
        survivor_id=survivor_id, loser_ids=loser_ids, chunk=chunk,
    )
    objects_repointed = _run_chunked_reconcile(
        session, _RECONCILE_OBJECT_ID,
        survivor_id=survivor_id, loser_ids=loser_ids, chunk=chunk,
    )
    if objects_repointed:
        logger.warning(
            "fact_reconcile: %d binary :Fact object_id repoint(s) survivor=%s "
            "losers=%s — fact_key left unrewritten (binary-fact key "
            "reconciliation deferred to the binary-derivation phase)",
            objects_repointed, survivor_id, loser_ids,
        )
    logger.debug(
        "fact_reconcile survivor=%s losers=%d subjects_repointed=%d "
        "facts_folded=%d objects_repointed=%d",
        survivor_id, len(loser_ids), subjects_repointed, facts_folded, objects_repointed,
    )
    return {
        "subjects_repointed": subjects_repointed,
        "facts_folded": facts_folded,
        "objects_repointed": objects_repointed,
    }


def reconcile_fact_subjects(
    driver_or_session,
    survivor_id: str,
    loser_ids: list[str],
    *,
    chunk_size: int | None = None,
) -> dict[str, int]:
    """Repoint merged-away entities' :Fact subject_id/object_id/uid.

    Runs AFTER ``app.db.neo4j.entity.merge_entities`` re-points the
    HAS_FACT/FACT_OBJECT EDGES for a loser onto the survivor — those
    re-points move the graph's pointers, but the :Fact node's own
    denormalised ``subject_id``/``uid`` (m0004's dedup identity,
    ``"{subject_id}|{fact_key}"``) still name the loser. Left alone, the
    survivor's next re-extraction would MERGE a *second* fact node for the
    same real-world fact instead of matching this one. This closes that gap.

    Per loser fact ``f`` with ``f.subject_id`` in ``loser_ids``:
      - No survivor collision: ``f.subject_id``/``f.uid`` rewritten in place.
      - Collision (a survivor fact ``g`` already carries the identical
        ``fact_key`` — the two entities asserted the same fact before the
        merge): ``f`` folds into ``g`` — interval union (open beats closed:
        NULL wins on ``valid_to``/``invalid_at``; the earlier non-empty
        ``valid_from`` wins) and source union (``'verification'`` wins,
        Risk R5) — then ``f``'s provenance/FACT_OBJECT edges move to ``g``
        and ``f`` is DETACH DELETEd. This is dedup collapse of one fact
        identity, the same class of operation ``merge_entities`` already
        performs on entity identity — NOT a belief revision (that needs
        Phase-D interval closure; see ``write_facts``'s
        ``facts_matched_closed`` telemetry).

    ``f.object_id`` in ``loser_ids`` re-points the property only — Phase C
    writes unary facts only (``object_id`` always NULL), so no live binary
    fact exists to safely rewrite ``uid``/``fact_key`` for; a
    ``logger.warning`` fires if this branch ever touches a row (binary-fact
    key reconciliation is deferred to the binary-derivation phase).

    Accepts either a Neo4j driver (opens + closes its own session) or an
    already-open session (used as-is — the caller keeps ownership, so
    ``merge_entities`` can thread its existing open session straight through
    without a nested open/close). Chunked at ``chunk_size`` (default
    ``config.ENTITY_MERGE_UNWIND_CHUNK``, entity.py's own merge-chunking
    knob) and idempotent — each statement's MATCH stops selecting a row once
    it's been reconciled, so re-running drains to zero.

    Multiple ``loser_ids`` reconciled in ONE call that happen to share a
    ``fact_key`` (only possible across DIFFERENT losers — a single subject
    can hold at most one fact per ``fact_key`` by the uid MERGE identity
    itself) can race for the same target uid within one batch; m0004's
    ``fact_uid_unique`` constraint turns that into a raised exception rather
    than silent corruption. ``merge_entities`` avoids the case entirely by
    calling this once per loser.

    Returns ``{"subjects_repointed", "facts_folded", "objects_repointed"}``.
    """
    if not loser_ids:
        return {"subjects_repointed": 0, "facts_folded": 0, "objects_repointed": 0}

    chunk = _reconcile_chunk_size(chunk_size)
    ids = list(loser_ids)
    session_factory = getattr(driver_or_session, "session", None)
    if callable(session_factory):
        with session_factory() as session:
            return _run_fact_reconcile(session, survivor_id, ids, chunk)
    return _run_fact_reconcile(driver_or_session, survivor_id, ids, chunk)
