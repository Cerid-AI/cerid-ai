# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Neo4j persistence for the bi-temporal :Fact layer (m0004/m0006).

Bi-temporal memory plan Phase C (C2) — the first (and only) writer of the
``:Fact`` nodes m0004 scaffolded. Idempotent MERGE of
:class:`core.agents.fact_derivation.DerivedFact` records:

    (:Entity)-[:HAS_FACT]->(:Fact)-[:FACT_OBJECT]->(:Entity)   // FACT_OBJECT binary-only
    (:Artifact)-[:FACT]->(:Fact)                               // provenance

Identity is m0004's single-property key ``uid``, and a node is one version: one
source memory's assertion about one subject,
``uid = "{subject_id}|{fact_key}|{source_artifact_id}"`` (spec §7, forget phase 5).
Re-extracting the same memory collapses onto its own node; a newer memory never
lands on the node an older one closed. ``fact_key`` encodes the EVENT-vs-STATE
split (event_date in the key for EVENT facts, absent for STATE), and symbolic
counts count distinct ``fact_key`` values (``fact_queries.count_facts``), so
several memories asserting one event still count once.

Bi-temporal stamps (the four-timestamp contract — see m0006 docstring):
``created_at`` = now (system time); ``valid_from`` = the memory's world-time
start; ``valid_to``/``invalid_at`` = null while the source memory is current.
Closure follows the memory's lineage: writing a version's facts closes the
STATE facts of the versions it superseded, and a fact whose source is already
superseded is written closed. EVENT facts never close. ``reconcile_fact_subjects``
(called from ``app.db.neo4j.entity.merge_entities``) rewrites a merged entity's
uids to match.

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
# holds mid-write. Each node is one version: one source memory's assertion
# about one subject (uid ends in the source artifact id), carrying the memory
# text as ``value`` and the memory's lineage. A row whose source memory no
# longer exists writes nothing: the memory was forgotten after its extraction
# was queued, and its text must not outlive it in a fact. FACT_OBJECT is
# written only for binary facts.
#
# A fact whose source memory is already superseded (its extraction ran late)
# is written closed at the time its memory stopped being true, so a late
# extraction can never leave an old value current.
_WRITE_FACTS_CYPHER = """
UNWIND $rows AS row
MATCH (a:Artifact {id: row.source_artifact_id})
WITH row, a,
     CASE WHEN a.superseded_by IS NOT NULL OR a.valid_to IS NOT NULL
          THEN coalesce(a.valid_to, $now) END AS closed_at
MERGE (subj:Entity {canonical_id: row.subject_id})
MERGE (f:Fact {uid: row.uid})
  ON CREATE SET
    f.subject_id = row.subject_id,
    f.object_id  = row.object_id,
    f.predicate  = row.predicate,
    f.fact_key   = row.fact_key,
    f.event_date = row.event_date,
    f.valid_from = row.valid_from,
    f.valid_to   = closed_at,
    f.invalid_at = CASE WHEN closed_at IS NULL THEN null ELSE $now END,
    f.closed_by  = CASE WHEN closed_at IS NULL THEN null ELSE a.superseded_by END,
    f.created_at = row.created_at,
    f.source     = row.source,
    f.source_artifact_id = row.source_artifact_id,
    f.lineage_id = coalesce(a.lineage_id, a.id),
    f.value      = row.value
MERGE (subj)-[:HAS_FACT]->(f)
WITH f, row, a
MERGE (a)-[:FACT]->(f)
FOREACH (oid IN CASE WHEN row.object_id IS NULL OR row.object_id = '' THEN [] ELSE [row.object_id] END |
  MERGE (obj:Entity {canonical_id: oid})
  MERGE (f)-[:FACT_OBJECT]->(obj)
)
RETURN count(DISTINCT f) AS facts_written,
       count(DISTINCT CASE WHEN f.invalid_at IS NOT NULL THEN f END) AS facts_matched_closed
"""

# Writing a version's facts is the step that knows the new value, so it closes
# the STATE facts of every superseded version in the same lineage (spec §7:
# never before the successor's value is known). ``closed_by`` names the version
# that replaced the source, which is what promotion reopens on a forget.
_CLOSE_PREDECESSOR_FACTS = """
MATCH (b:Artifact {id: $aid})
WHERE b.lineage_id IS NOT NULL AND b.superseded_by IS NULL AND b.valid_to IS NULL
MATCH (p) WHERE (p:Artifact OR p:Memory) AND p.lineage_id = b.lineage_id AND p.id <> b.id
  AND (p.superseded_by IS NOT NULL OR p.valid_to IS NOT NULL)
MATCH (f:Fact {source_artifact_id: p.id})
WHERE f.valid_to IS NULL AND f.predicate IN $state
SET f.valid_to = coalesce(p.valid_to, $now), f.invalid_at = $now, f.closed_by = p.superseded_by
RETURN count(f) AS closed
"""

FACT_VALUE_MAX = 500


def _build_rows(
    facts: Sequence[DerivedFact], *, source_artifact_id: str, created_at: str, value: str = "",
) -> list[dict]:
    """Materialise write rows, deduplicated by ``uid`` (identical facts collapse
    before the UNWIND — belt-and-braces with the DB-level MERGE dedup)."""
    by_uid: dict[str, dict] = {}
    for fact in facts:
        uid = fact_uid(fact.subject_id, fact.fact_key, source_artifact_id)
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
                "created_at": created_at,
                "source": fact.source,
                "source_artifact_id": source_artifact_id,
                "value": value[:FACT_VALUE_MAX],
            },
        )
    return list(by_uid.values())


def write_facts(
    driver,
    facts: Sequence[DerivedFact],
    *,
    source_artifact_id: str,
    value: str = "",
    chunk_size: int = FACT_WRITE_CHUNK_SIZE,
) -> dict[str, int]:
    """MERGE ``facts`` as bi-temporal ``(:Fact)`` versions for one source
    memory, then close the STATE facts of the versions it superseded.

    Idempotent: re-running with the same facts collapses to the same nodes via
    the ``uid`` MERGE (only ``ON CREATE`` sets the stamps). Returns
    ``{"facts_written", "facts_matched_closed", "facts_closed", "chunks"}``;
    ``facts_matched_closed`` counts facts written closed because their source
    was already superseded. Lets store exceptions propagate — the caller (the
    entity-extraction job's fact step) wraps this in ``log_swallowed_error`` so a
    fact-write failure never loses the already-successful entity extraction.
    """
    now = utcnow_iso()
    rows = _build_rows(facts, source_artifact_id=source_artifact_id, created_at=now, value=value)
    written = 0
    matched_closed = 0
    chunks = 0
    with driver.session() as session:
        for start in range(0, len(rows), chunk_size):
            batch = rows[start : start + chunk_size]
            row = session.run(_WRITE_FACTS_CYPHER, rows=batch, now=now).single()
            if row is not None:
                written += int(row["facts_written"])
                matched_closed += int(row["facts_matched_closed"])
            chunks += 1
    closed = close_superseded_facts(driver, source_artifact_id)

    logger.debug(
        "facts_written artifact=%s facts=%d matched_closed=%d closed=%d chunks=%d",
        source_artifact_id, written, matched_closed, closed, chunks,
    )
    return {
        "facts_written": written,
        "facts_matched_closed": matched_closed,
        "facts_closed": closed,
        "chunks": chunks,
    }


def close_superseded_facts(driver, artifact_id: str) -> int:
    """Close the open STATE facts of the versions ``artifact_id`` superseded.
    Run when a version's facts are written, including when it yields none."""
    from core.lineage.writer import state_predicates

    with driver.session() as session:
        row = session.run(
            _CLOSE_PREDECESSOR_FACTS, aid=artifact_id, state=state_predicates(), now=utcnow_iso(),
        ).single()
    return int(row["closed"]) if row is not None else 0


# ===========================================================================
# Phase D — post-merge :Fact subject/object property reconciliation
# ===========================================================================
#
# app.db.neo4j.entity.merge_entities re-points the HAS_FACT/FACT_OBJECT EDGES
# onto the survivor (edge-only — see the comment there); it does not touch the
# :Fact node's own denormalised subject_id/object_id/uid properties, because
# that is this writer's dedup contract (m0004's uid = "{subject_id}|
# {fact_key}|{source_artifact_id}"). This is that contract's other half: called once per loser,
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
WITH f, $survivor_id + '|' + f.fact_key
     + CASE WHEN coalesce(f.source_artifact_id, '') = '' THEN '' ELSE '|' + f.source_artifact_id END AS new_uid
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
WITH f, $survivor_id + '|' + f.fact_key
     + CASE WHEN coalesce(f.source_artifact_id, '') = '' THEN '' ELSE '|' + f.source_artifact_id END AS new_uid
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
