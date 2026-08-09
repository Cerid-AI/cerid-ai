# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Bi-temporal interval closure for a superseded memory (plan Phase D, D1+D2).

Called by the memory-consolidation path *after* it has already decided a memory
is superseded (an UPDATE — ``mark_superseded`` has set ``old.superseded_by``).
This module closes the corresponding STATE fact intervals in BOTH stores so the
Neo4j ``:Fact`` layer and the Chroma memory store never diverge (plan risks
R4/R7). Closure is interval-closure, never deletion — a closed fact keeps its
``valid_from`` and gains a ``valid_to``, so "as-of T" history stays queryable.

Timestamps follow the CANONICAL FOUR-TIMESTAMP CONTRACT (see
``app/db/neo4j/migrations/m0006_fact_bitemporal.py``): CODE sets all four; the
LLM/extractor never writes them. Closure sets ``valid_to`` (WORLD time — when
the fact stopped being true) and ``invalid_at`` (SYSTEM time — when we stopped
believing it). ``valid_to`` = the superseding memory's ``valid_from`` when
known, else the observation instant (mirrors
``fact_derivation.resolve_valid_from``'s ambiguity default).

Two ratified deviations from the plan letter (owner-ratified — do not
re-litigate):

1. CLOSURE FOLLOWS THE SUPERSESSION DECISION UNCONDITIONALLY — it is NOT gated
   on NLI. ``mark_superseded`` already set ``Artifact.superseded_by`` from the
   trusted conflict-resolution decision; gating fact closure on a second NLI
   pass could leave the ``:Fact`` layer disagreeing with the memory layer (the
   stores must never diverge). NLI is used only to CLASSIFY the closure for
   ledger routing (D2): a genuine disagreement is surfaced on the contradiction
   ledger; an orderly knowledge-update is closed silently, no ledger entry.

2. UNARY-FACT RE-ASSERTION / RE-OPENING IS DEFERRED — a closed fact stays
   closed. This module never re-opens an interval. If a later memory re-asserts
   a previously-closed value, the writer's own MERGE handles it (a re-asserted
   value is a distinct live fact); the writer's "matched-closed" telemetry
   (owned by the fact-writer, ``app/db/neo4j/facts.py``) is the signal for that
   case, not this module.
"""
from __future__ import annotations

import asyncio
import logging

import config
from core.agents.hallucination.grounding_verifier import NLI_PREMISE_CHAR_LIMIT
from core.utils.nli import batch_nli_score
from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.fact_invalidation")

# Close only STATE fact intervals (empirical/decision/preference — the
# supersedable current-state types). EVENT facts (every other predicate) are
# dated occurrences that coexist and must never be closed by a newer value —
# the ``f.predicate IN $state_predicates`` guard below leaves them untouched.
# Mirrors fact_derivation's STATE/EVENT split so the two stores agree.
#
# Close a STATE fact only when it has NO other LIVE provenance: an artifact
# other than the superseded one, still un-superseded, keeps the fact alive.
# ``old`` itself is already superseded (mark_superseded set its
# ``superseded_by``), so it is excluded from the liveness check.
_CLOSE_INTERVALS_CYPHER = """
MATCH (old:Artifact {id: $old_id})-[:FACT]->(f:Fact)
WHERE f.invalid_at IS NULL
  AND f.predicate IN $state_predicates
  AND NOT EXISTS {
    MATCH (o:Artifact)-[:FACT]->(f)
    WHERE o.id <> $old_id AND o.superseded_by IS NULL
  }
SET f.valid_to = $valid_to, f.invalid_at = $now
RETURN count(f) AS closed
"""


def _close_neo4j_intervals(
    driver,
    *,
    old_artifact_id: str,
    state_predicates: list[str],
    valid_to: str,
    now: str,
) -> int:
    """Sync Neo4j interval closure — run in a worker thread. Mirrors
    ``mark_superseded``'s inline-Cypher-on-driver style (core/ never imports
    app/). Returns the count of STATE facts closed."""
    with driver.session() as session:
        row = session.run(
            _CLOSE_INTERVALS_CYPHER,
            old_id=old_artifact_id,
            state_predicates=state_predicates,
            valid_to=valid_to,
            now=now,
        ).single()
    return int(row["closed"]) if row is not None else 0


def _mirror_close_chroma(
    collection,
    *,
    old_artifact_id: str,
    valid_to: str,
) -> str:
    """Sync Chroma mirror-close — run in a worker thread (plan C3 lockstep).

    Closes ``valid_to`` on every chunk of the superseded artifact and returns
    its first non-empty chunk document (for the D2 NLI classification). Only
    ``valid_to`` is touched — every other metadata key is preserved, especially
    ``decay_anchor`` (the i20b decay contract must not be affected in any way).
    """
    res = collection.get(
        where={"artifact_id": {"$eq": old_artifact_id}},
        include=["metadatas", "documents"],
    )
    ids = list(res.get("ids", []) or [])
    if not ids:
        return ""
    metadatas = list(res.get("metadatas", []) or [])
    documents = list(res.get("documents", []) or [])

    updated = []
    for meta in metadatas:
        new_meta = dict(meta or {})
        new_meta["valid_to"] = valid_to
        updated.append(new_meta)
    if updated:
        collection.update(ids=ids, metadatas=updated)

    for doc in documents:
        if doc and doc.strip():
            return doc
    return ""


async def _route_to_ledger(
    *,
    old_content: str,
    new_content: str,
    old_artifact_id: str,
) -> None:
    """D2 routing: classify the closure as orderly-update vs genuine
    disagreement via NLI, and persist genuine disagreements to the
    contradiction ledger (mirrors ``verification.py`` sink invocation).

    Best-effort throughout: NLI failure or a missing sink leaves the (already
    applied) closure intact — closure must never depend on NLI availability.
    The ledger is the only consumer of the NLI signal here, so when it is
    disabled the classification is skipped entirely.
    """
    if not config.ENABLE_CONTRADICTION_LEDGER:
        return

    old_slice = (old_content or "")[:NLI_PREMISE_CHAR_LIMIT]
    new_slice = (new_content or "")[:NLI_PREMISE_CHAR_LIMIT]
    if not old_slice.strip() or not new_slice.strip():
        return  # nothing to classify — orderly closure only

    try:
        scores = await asyncio.to_thread(batch_nli_score, [(old_slice, new_slice)])
    except Exception as exc:  # noqa: BLE001 — closure must not depend on NLI
        log_swallowed_error("core.agents.fact_invalidation.nli", exc)
        return

    contradiction = float(scores[0]["contradiction"]) if scores else 0.0
    if contradiction < config.NLI_CONTRADICTION_THRESHOLD:
        return  # orderly knowledge-update — closure only, no ledger entry

    # Genuine disagreement — surface on the contradiction ledger. Gated +
    # best-effort; the sink is wired from app startup (core/ cannot import
    # app.services.contradiction_log). Mirrors verification.py's invocation.
    from core.agents.hallucination.contradiction_sink import get_contradiction_sink

    _csink = get_contradiction_sink()
    if _csink is not None:
        try:
            await _csink(
                claim_text=new_content[:500],
                source_text=old_content[:500],
                source_artifact_id=old_artifact_id,
                severity="medium",
            )
        except Exception as exc:  # noqa: BLE001 — ledger write must not block closure
            log_swallowed_error(
                "core.agents.fact_invalidation.contradiction_sink", exc
            )


async def close_superseded_memory_intervals(
    neo4j_driver,
    chroma_collection,
    *,
    old_artifact_id: str,
    new_artifact_id: str,
    new_valid_from: str | None,
    new_content: str,
    now: str | None = None,
) -> int:
    """Close the STATE fact intervals of a memory the caller already superseded.

    Runs after ``mark_superseded`` on the UPDATE path. Closes the old artifact's
    STATE facts in Neo4j (``:Fact.valid_to``/``invalid_at``) and mirror-closes
    the same ``valid_to`` on its Chroma chunks, then routes the closure to the
    contradiction ledger when NLI confirms a genuine disagreement (D2). EVENT
    facts and any STATE fact still held live by another artifact are untouched.

    Every step is best-effort and offloaded to a worker thread (the caller is on
    the event loop); the whole function is additionally wrapped best-effort at
    the call site. Returns the count of Neo4j STATE facts closed (telemetry).
    """
    now_iso = now or utcnow_iso()
    # World-time close point: the superseding memory's valid_from, else the
    # observation instant (world time unknown → observation default).
    valid_to = (new_valid_from or "").strip() or now_iso
    state_predicates = sorted(config.settings.MEMORY_POWER_LAW_TYPES)

    # 1. Neo4j interval closure — authoritative, follows the supersession
    #    decision unconditionally (deviation 1). No-op until the fact writer
    #    (ENABLE_FACT_WRITES) has populated :Fact nodes.
    closed = 0
    if neo4j_driver is not None:
        try:
            closed = await asyncio.to_thread(
                _close_neo4j_intervals,
                neo4j_driver,
                old_artifact_id=old_artifact_id,
                state_predicates=state_predicates,
                valid_to=valid_to,
                now=now_iso,
            )
        except Exception as exc:  # noqa: BLE001 — closure best-effort; store path must not break
            log_swallowed_error(
                "core.agents.fact_invalidation.neo4j_close", exc
            )

    # 2. Chroma mirror-close (plan C3 lockstep) — also yields the old content
    #    slice the D2 NLI classification needs. Best-effort.
    old_content = ""
    if chroma_collection is not None:
        try:
            old_content = await asyncio.to_thread(
                _mirror_close_chroma,
                chroma_collection,
                old_artifact_id=old_artifact_id,
                valid_to=valid_to,
            )
        except Exception as exc:  # noqa: BLE001 — Chroma close best-effort
            log_swallowed_error(
                "core.agents.fact_invalidation.chroma_close", exc
            )

    # 3. D2 — route genuine disagreements to the contradiction ledger.
    await _route_to_ledger(
        old_content=old_content,
        new_content=new_content,
        old_artifact_id=old_artifact_id,
    )

    logger.debug(
        "fact_invalidation closed=%d old=%s new=%s valid_to=%s",
        closed, old_artifact_id, new_artifact_id, valid_to,
    )
    return closed
