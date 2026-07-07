# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j adapter for ``(:Brief)`` nodes.

Schema
------
::

    (:Brief {
        brief_id,        # UUID string — unique constraint
        kind,            # "daily" | "weekly"
        generated_at,    # ISO-8601 UTC string
        prompt_version,  # e.g. "daily-v1"
        status,          # "pending" | "generated" | "failed" | "snoozed"
        sections_json    # JSON-serialised {section_name: markdown_text}
    })
    (:Brief)-[:CITES]->(:Claim)  // one edge per claim_id in record.claim_ids

Functions
---------
``save_brief(driver, record)``   — upsert a Brief node + CITES edges.
``save_verified_claims(driver, claims)`` — write text/band (+ best-effort
``EXTRACTED_FROM`` edges) for claims surfaced by the generation-time
verification pass (Task 2.1b).
``list_briefs(driver, kind, limit)`` — return recent briefs as BriefRecord list.
``get_brief(driver, brief_id)``  — fetch a single brief, with cited claim ids.
``hydrate_claims(driver, brief_id)`` — per-claim text/band/source_ids for a brief.
``hydrate_claims_for_briefs(driver, brief_ids)`` — batch counterpart to
``hydrate_claims``, one round-trip for many briefs.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from core.utils.swallowed import log_swallowed_error

if TYPE_CHECKING:
    from app.services.briefs.service import BriefRecord

logger = logging.getLogger("ai-companion.briefs.neo4j")


# ---------------------------------------------------------------------------
# Schema bootstrap (idempotent)
# ---------------------------------------------------------------------------


def ensure_brief_schema(driver: Any) -> None:
    """Create Brief constraint and indexes. Safe to call on every startup."""
    with driver.session() as session:
        session.run(
            "CREATE CONSTRAINT brief_id_unique IF NOT EXISTS "
            "FOR (b:Brief) REQUIRE b.brief_id IS UNIQUE"
        )
        session.run(
            "CREATE INDEX brief_kind_idx IF NOT EXISTS "
            "FOR (b:Brief) ON (b.kind)"
        )
        session.run(
            "CREATE INDEX brief_generated_at_idx IF NOT EXISTS "
            "FOR (b:Brief) ON (b.generated_at)"
        )
    logger.info("Brief schema ensured (constraint + 2 indexes)")


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def save_brief(driver: Any, record: "BriefRecord") -> None:
    """Upsert a ``(:Brief)`` node and ``[:CITES]->(:Claim)`` edges.

    The ``sections`` dict is serialised to JSON because Neo4j does not
    support nested map values on nodes.
    """
    from core.utils.time import utcnow_iso  # type: ignore[import-untyped]

    generated_at_str = (
        record.generated_at.astimezone(timezone.utc).isoformat()
        if isinstance(record.generated_at, datetime)
        else str(record.generated_at)
    )

    with driver.session() as session:
        session.run(
            """
            MERGE (b:Brief {brief_id: $brief_id})
            SET b.kind           = $kind,
                b.generated_at   = $generated_at,
                b.prompt_version = $prompt_version,
                b.status         = $status,
                b.sections_json  = $sections_json,
                b.updated_at     = $updated_at
            """,
            brief_id=record.brief_id,
            kind=record.kind,
            generated_at=generated_at_str,
            prompt_version=record.prompt_version,
            status=record.status,
            sections_json=json.dumps(record.sections),
            updated_at=utcnow_iso(),
        )

        # Create CITES edges for each claim_id.  MERGE on the Claim node so
        # we don't require it to exist yet (the claim may arrive later).
        for claim_id in record.claim_ids:
            session.run(
                """
                MATCH (b:Brief {brief_id: $brief_id})
                MERGE (c:Claim {claim_id: $claim_id})
                MERGE (b)-[:CITES]->(c)
                """,
                brief_id=record.brief_id,
                claim_id=claim_id,
            )

    logger.debug(
        "saved Brief brief_id=%s kind=%s status=%s claims=%d",
        record.brief_id,
        record.kind,
        record.status,
        len(record.claim_ids),
    )


def save_verified_claims(driver: Any, claims: list[dict[str, Any]]) -> None:
    """Persist banded claim text + best-effort source edges (Task 2.1b).

    ``save_brief`` only MERGEs the ``:Claim`` node by ``claim_id`` when
    wiring ``[:CITES]`` edges — it never writes ``text``/``band``. This
    is the write path the generation-time verification pass
    (:func:`app.services.briefs.verification.verify_brief_claims`) uses
    so the read API's ``hydrate_claims`` returns real data instead of
    nulls. Source edges are best-effort: a claim with no ``source_ids``,
    or a ``source_id`` with no matching ``:Artifact`` node (e.g. an
    orphaned vector/memory-collection id never mirrored to the graph),
    simply gets no ``[:EXTRACTED_FROM]`` edge — the artifact side is
    ``MATCH``-only so this write path never creates bare stub Artifact
    nodes.
    """
    from core.utils.time import utcnow_iso  # type: ignore[import-untyped]

    if not claims:
        return

    now = utcnow_iso()
    with driver.session() as session:
        for claim in claims:
            session.run(
                """
                MERGE (c:Claim {claim_id: $claim_id})
                SET c.text       = $text,
                    c.band       = $band,
                    c.updated_at = $updated_at
                """,
                claim_id=claim["claim_id"],
                text=claim["text"],
                band=claim["band"],
                updated_at=now,
            )
            for source_id in claim.get("source_ids") or []:
                try:
                    session.run(
                        """
                        MATCH (c:Claim {claim_id: $claim_id})
                        MATCH (a:Artifact {id: $source_id})
                        MERGE (c)-[:EXTRACTED_FROM]->(a)
                        """,
                        claim_id=claim["claim_id"],
                        source_id=source_id,
                    )
                except Exception as exc:  # noqa: BLE001 — best-effort per source edge
                    log_swallowed_error(
                        "app.db.neo4j.briefs.extracted_from",
                        exc,
                        context={"claim_id": claim["claim_id"], "source_id": source_id},
                    )
                    continue

    logger.debug("saved %d verified claim(s)", len(claims))


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def list_briefs(
    driver: Any,
    *,
    kind: str,
    limit: int = 20,
) -> list["BriefRecord"]:
    """Return up to ``limit`` recent briefs of the given kind.

    Results are ordered by ``generated_at`` descending (most recent first).
    """
    from app.services.briefs.service import BriefRecord  # local import avoids circular

    with driver.session() as session:
        result = session.run(
            """
            MATCH (b:Brief {kind: $kind})
            RETURN b
            ORDER BY b.generated_at DESC
            LIMIT $limit
            """,
            kind=kind,
            limit=limit,
        )
        records: list[BriefRecord] = []
        for row in result:
            node = row["b"]
            try:
                sections: dict[str, str] = json.loads(node.get("sections_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                sections = {}
            try:
                generated_at = datetime.fromisoformat(node["generated_at"])
            except (KeyError, ValueError):
                generated_at = datetime.now(tz=timezone.utc)
            records.append(
                BriefRecord(
                    brief_id=node["brief_id"],
                    kind=node["kind"],
                    generated_at=generated_at,
                    prompt_version=node.get("prompt_version", ""),
                    sections=sections,
                    claim_ids=[],  # CITES edges not hydrated in list view
                    status=node.get("status", "generated"),
                )
            )
        return records


def get_brief(driver: Any, brief_id: str) -> "BriefRecord | None":
    """Fetch a single brief by ``brief_id``, hydrating cited claim ids.

    Returns ``None`` when no ``(:Brief)`` node matches. Claim ids come
    from the ``[:CITES]`` edges; use :func:`hydrate_claims` for full
    per-claim detail (text/band/source_ids).
    """
    from app.services.briefs.service import BriefRecord  # local import avoids circular

    with driver.session() as session:
        result = session.run(
            """
            MATCH (b:Brief {brief_id: $brief_id})
            OPTIONAL MATCH (b)-[:CITES]->(c:Claim)
            RETURN b, collect(c.claim_id) AS claim_ids
            """,
            brief_id=brief_id,
        )
        row = result.single()
        if not row:
            return None
        node = row["b"]
        try:
            sections: dict[str, str] = json.loads(node.get("sections_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            sections = {}
        try:
            generated_at = datetime.fromisoformat(node["generated_at"])
        except (KeyError, ValueError):
            generated_at = datetime.now(tz=timezone.utc)
        return BriefRecord(
            brief_id=node["brief_id"],
            kind=node["kind"],
            generated_at=generated_at,
            prompt_version=node.get("prompt_version", ""),
            sections=sections,
            claim_ids=[cid for cid in row["claim_ids"] if cid],
            status=node.get("status", "generated"),
        )


def hydrate_claims(driver: Any, brief_id: str) -> list[dict[str, Any]]:
    """Return per-claim detail for every claim a brief cites.

    Each entry has ``claim_id``, ``text`` (nullable — :func:`save_verified_claims`
    is the write path, populated only for claims that went through the
    generation-time verification pass; a claim cited via ``[:CITES]`` that
    verification never surfaced stays null), ``band`` (nullable for the
    same reason; callers must default a missing band rather than infer
    "verified"), and ``source_ids`` (artifact ids reached via
    ``[:EXTRACTED_FROM]``).
    """
    with driver.session() as session:
        result = session.run(
            """
            MATCH (b:Brief {brief_id: $brief_id})-[:CITES]->(c:Claim)
            OPTIONAL MATCH (c)-[:EXTRACTED_FROM]->(a:Artifact)
            RETURN c.claim_id AS claim_id, c.text AS text, c.band AS band,
                   collect(a.id) AS source_ids
            """,
            brief_id=brief_id,
        )
        claims: list[dict[str, Any]] = []
        for row in result:
            claims.append(
                {
                    "claim_id": row["claim_id"],
                    "text": row["text"],
                    "band": row["band"],
                    "source_ids": [aid for aid in row["source_ids"] if aid],
                }
            )
        return claims


def hydrate_claims_for_briefs(
    driver: Any, brief_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Batch counterpart to :func:`hydrate_claims` — one round-trip for many briefs.

    Returns ``{brief_id: [claim, ...]}`` covering every id in ``brief_ids``.
    A brief with no ``[:CITES]`` edges maps to ``[]`` — the ``OPTIONAL MATCH``
    null-claim row is filtered out here rather than surfaced as a claim
    entry with a ``None`` ``claim_id``.
    """
    if not brief_ids:
        return {}

    with driver.session() as session:
        result = session.run(
            """
            MATCH (b:Brief) WHERE b.brief_id IN $ids
            OPTIONAL MATCH (b)-[:CITES]->(c:Claim)
            OPTIONAL MATCH (c)-[:EXTRACTED_FROM]->(a:Artifact)
            WITH b, c, collect(a.id) AS source_ids
            RETURN b.brief_id AS brief_id,
                   collect({claim_id: c.claim_id, text: c.text, band: c.band,
                            source_ids: source_ids}) AS claims
            """,
            ids=brief_ids,
        )
        out: dict[str, list[dict[str, Any]]] = {}
        for row in result:
            claims_for_brief: list[dict[str, Any]] = []
            for raw in row["claims"]:
                if raw["claim_id"] is None:
                    # The brief cites nothing; OPTIONAL MATCH still yields
                    # one row with every field null — drop it rather than
                    # surface a phantom claim.
                    continue
                claims_for_brief.append(
                    {
                        "claim_id": raw["claim_id"],
                        "text": raw["text"],
                        "band": raw["band"],
                        "source_ids": [aid for aid in raw["source_ids"] if aid],
                    }
                )
            out[row["brief_id"]] = claims_for_brief
        return out
