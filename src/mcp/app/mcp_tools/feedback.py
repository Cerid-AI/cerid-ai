# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Phase 5 active-learning tools — 4 tools.

User feedback that flows back into the KB and influences retrieval +
trust scoring. Together these tools wire the schema that
``trust_score.user_agreement`` and ``trust_score.verification_coverage``
were designed for — the :RATED edge + endorsement_weight property are
the previously-missing inputs.

* ``pkb_rate`` — record a sentiment rating on a Claim (:RATED edge).
* ``pkb_correct`` — submit a correction tied to one artifact (:Correction).
* ``pkb_endorse`` — boost an artifact's relevance score
  (``endorsement_weight`` on :Artifact).
* ``pkb_flag`` — mark an artifact as inaccurate / outdated / off-topic /
  duplicate / spam (``flag_reason`` on :Artifact).

Schema additions land in ``app/db/neo4j/schema.py::init_schema`` so
they're created idempotently on every container boot. No standalone
migration script needed.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from app.deps import get_neo4j
from app.tool_registry import (
    InvalidParamsError,
    ResourceNotFoundError,
    UpstreamUnavailableError,
    register_tool,
)
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.mcp_tools.feedback")


_FLAG_REASONS = frozenset({"inaccurate", "outdated", "off_topic", "duplicate", "spam"})


# ============================================================ pkb_rate


@register_tool(
    name="pkb_rate",
    description=(
        "Record a sentiment rating on a Claim node. Sentiment ∈ "
        "{-1, 0, 1} (negative, neutral, positive). Optional `note` "
        "captures rationale. **Use when** the user reacts to a "
        "specific claim (e.g. 'this is wrong', 'verified'). "
        "**Returns** `{rated, claim_id, sentiment, ts}`. Errors -32004 "
        "if the claim doesn't exist. Creates :RATED edges that feed "
        "`trust_score.user_agreement` directly — no other plumbing "
        "required."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "claim_id": {
                "type": "string",
                "description": "Claim node id (from pkb_extract_claims or hallucination check)",
            },
            "sentiment": {
                "type": "integer",
                "enum": [-1, 0, 1],
                "description": "-1 = disagree, 0 = neutral, 1 = agree",
            },
            "note": {
                "type": "string",
                "description": "Optional rationale (<=500 chars)",
                "default": "",
            },
        },
        "required": ["claim_id", "sentiment"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "rated": {"type": "boolean"},
            "claim_id": {"type": "string"},
            "sentiment": {"type": "integer"},
            "ts": {"type": "string"},
        },
    },
    cost_class="low",
)
async def pkb_rate(
    claim_id: str,
    sentiment: int,
    note: str = "",
) -> dict[str, Any]:
    if sentiment not in (-1, 0, 1):
        raise InvalidParamsError("sentiment must be -1, 0, or 1")
    if len(note) > 500:
        raise InvalidParamsError("note must be <=500 chars")

    driver = get_neo4j()
    now = utcnow_iso()

    def _run() -> bool:
        with driver.session() as session:
            # MERGE the Claim so the rating works even when no upstream
            # path has materialised it as a :Claim node yet — Cerid's
            # verification pipeline currently persists :VerificationReport
            # nodes with claims-as-JSON-blob rather than first-class
            # :Claim records. pkb_rate creates the standalone Claim on
            # first reference (with created_at + first_rated_at) and
            # MERGEs the :RATED edge from an anonymous Rater so
            # re-ratings update rather than duplicate.
            #
            # The created_at property is what trust_score's user_agreement
            # reader filters on via claim_accuracy_rolling — without it
            # the rating exists but is invisible to the 7-day rolling
            # aggregate.
            result = session.run(
                """
                MERGE (c:Claim {claim_id: $claim_id})
                  ON CREATE SET c.created_at = $ts, c.first_rated_at = $ts
                MERGE (rater:Rater {scope: 'anonymous'})
                MERGE (rater)-[r:RATED]->(c)
                SET r.sentiment = $sentiment,
                    r.note = $note,
                    r.ts = $ts
                RETURN c.claim_id AS claim_id
                """,
                claim_id=claim_id,
                sentiment=sentiment,
                note=note,
                ts=now,
            )
            return result.single() is not None

    try:
        rated = await asyncio.to_thread(_run)
    except Exception as exc:
        raise UpstreamUnavailableError(f"Neo4j unreachable: {exc}") from exc

    if not rated:
        # Defensive — MERGE always returns a row, so this is unreachable
        # in practice. Kept for symmetry with the other error envelopes.
        raise ResourceNotFoundError(f"Claim {claim_id!r} not found")

    return {"rated": True, "claim_id": claim_id, "sentiment": sentiment, "ts": now}


# ============================================================ pkb_correct


@register_tool(
    name="pkb_correct",
    description=(
        "Submit a correction tied to one artifact. The correction is "
        "stored as a :Correction node linked to the :Artifact via "
        "ATTACHED_TO so it surfaces in `pkb_artifact_get`. Optional "
        "`applies_to_chunk_id` scopes the correction to one chunk. "
        "**Use when** the user notes an error / out-of-date fact in "
        "an ingested artifact and wants it tracked. **Returns** "
        "`{correction_id, artifact_id, ts}`. Errors -32004 on missing "
        "artifact."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string"},
            "correction_text": {
                "type": "string",
                "description": "The user's correction (1-2000 chars)",
            },
            "applies_to_chunk_id": {
                "type": "string",
                "description": "Optional chunk-scoped correction id",
                "default": "",
            },
        },
        "required": ["artifact_id", "correction_text"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "correction_id": {"type": "string"},
            "artifact_id": {"type": "string"},
            "applies_to_chunk_id": {"type": "string"},
            "ts": {"type": "string"},
        },
    },
    cost_class="low",
)
async def pkb_correct(
    artifact_id: str,
    correction_text: str,
    applies_to_chunk_id: str = "",
) -> dict[str, Any]:
    if not correction_text.strip():
        raise InvalidParamsError("correction_text must be non-empty")
    if len(correction_text) > 2000:
        raise InvalidParamsError("correction_text must be <=2000 chars")

    driver = get_neo4j()
    correction_id = str(uuid.uuid4())
    now = utcnow_iso()

    def _run() -> bool:
        with driver.session() as session:
            # Confirm artifact exists; -32004 if not.
            check = session.run(
                "MATCH (a:Artifact {id: $aid}) RETURN count(a) AS c",
                aid=artifact_id,
            )
            if int(check.single()["c"]) == 0:
                return False

            session.run(
                """
                MATCH (a:Artifact {id: $aid})
                CREATE (c:Correction {
                    id: $cid,
                    artifact_id: $aid,
                    text: $text,
                    applies_to_chunk_id: $chunk,
                    ts: $ts
                })
                CREATE (c)-[:ATTACHED_TO]->(a)
                """,
                aid=artifact_id, cid=correction_id, text=correction_text,
                chunk=applies_to_chunk_id, ts=now,
            )
            return True

    try:
        ok = await asyncio.to_thread(_run)
    except Exception as exc:
        raise UpstreamUnavailableError(f"Neo4j unreachable: {exc}") from exc

    if not ok:
        raise ResourceNotFoundError(f"Artifact {artifact_id!r} not found")

    return {
        "correction_id": correction_id,
        "artifact_id": artifact_id,
        "applies_to_chunk_id": applies_to_chunk_id,
        "ts": now,
    }


# ============================================================ pkb_endorse


@register_tool(
    name="pkb_endorse",
    description=(
        "Boost an artifact's retrieval-time relevance by setting its "
        "`endorsement_weight`. Default 1.0; positive weights >1 promote "
        "the artifact in reranking, weights <1 demote it. **Use when** "
        "the user explicitly endorses (`weight=2.0`) or de-emphasises "
        "(`weight=0.5`) one artifact. **Returns** `{artifact_id, "
        "endorsement_weight, ts}`. Errors -32004 on missing."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string"},
            "weight": {
                "type": "number",
                "description": "0.1–10.0; default 2.0 (endorse). Set <1 to demote.",
                "default": 2.0,
            },
        },
        "required": ["artifact_id"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string"},
            "endorsement_weight": {"type": "number"},
            "previous_weight": {"type": "number"},
            "ts": {"type": "string"},
        },
    },
    cost_class="low",
)
async def pkb_endorse(
    artifact_id: str,
    weight: float = 2.0,
) -> dict[str, Any]:
    if not (0.1 <= weight <= 10.0):
        raise InvalidParamsError("weight must be in [0.1, 10.0]")

    driver = get_neo4j()
    now = utcnow_iso()

    def _run() -> tuple[bool, float]:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (a:Artifact {id: $id})
                WITH a, coalesce(a.endorsement_weight, 1.0) AS prev
                SET a.endorsement_weight = $weight,
                    a.endorsed_at = $ts
                RETURN prev
                """,
                id=artifact_id, weight=weight, ts=now,
            )
            row = result.single()
            if row is None:
                return False, 1.0
            return True, float(row["prev"])

    try:
        ok, prev = await asyncio.to_thread(_run)
    except Exception as exc:
        raise UpstreamUnavailableError(f"Neo4j unreachable: {exc}") from exc

    if not ok:
        raise ResourceNotFoundError(f"Artifact {artifact_id!r} not found")

    return {
        "artifact_id": artifact_id,
        "endorsement_weight": weight,
        "previous_weight": prev,
        "ts": now,
    }


# ============================================================ pkb_flag


@register_tool(
    name="pkb_flag",
    description=(
        "Mark an artifact as questionable / outdated / off-topic / "
        "duplicate / spam. Flagged artifacts are excluded from default "
        "retrieval (unless the caller passes `include_flagged=true` "
        "downstream). **Use when** the user identifies a problem with "
        "an artifact but doesn't want to hard-delete it. **Returns** "
        "`{artifact_id, flag_reason, ts}`. Pass `flag_reason=''` to "
        "clear an existing flag. Valid reasons: inaccurate, outdated, "
        "off_topic, duplicate, spam."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string"},
            "reason": {
                "type": "string",
                "description": (
                    "One of inaccurate / outdated / off_topic / duplicate / "
                    "spam. Empty string clears any existing flag."
                ),
            },
            "note": {
                "type": "string",
                "description": "Optional rationale (<=500 chars)",
                "default": "",
            },
        },
        "required": ["artifact_id", "reason"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string"},
            "flag_reason": {"type": "string"},
            "previous_flag": {"type": "string"},
            "ts": {"type": "string"},
        },
    },
    cost_class="low",
)
async def pkb_flag(
    artifact_id: str,
    reason: str,
    note: str = "",
) -> dict[str, Any]:
    if reason and reason not in _FLAG_REASONS:
        raise InvalidParamsError(
            f"reason must be one of {sorted(_FLAG_REASONS)} or empty; got {reason!r}"
        )
    if len(note) > 500:
        raise InvalidParamsError("note must be <=500 chars")

    driver = get_neo4j()
    now = utcnow_iso()
    # Empty reason → clear flag (set to null).
    set_reason = reason if reason else None

    def _run() -> tuple[bool, str]:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (a:Artifact {id: $id})
                WITH a, coalesce(a.flag_reason, '') AS prev
                SET a.flag_reason = $reason,
                    a.flag_note = $note,
                    a.flagged_at = $ts
                RETURN prev
                """,
                id=artifact_id, reason=set_reason, note=note, ts=now,
            )
            row = result.single()
            if row is None:
                return False, ""
            return True, str(row["prev"] or "")

    try:
        ok, prev = await asyncio.to_thread(_run)
    except Exception as exc:
        raise UpstreamUnavailableError(f"Neo4j unreachable: {exc}") from exc

    if not ok:
        raise ResourceNotFoundError(f"Artifact {artifact_id!r} not found")

    return {
        "artifact_id": artifact_id,
        "flag_reason": reason,
        "previous_flag": prev,
        "ts": now,
    }
