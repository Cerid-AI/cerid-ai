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
``list_briefs(driver, kind, limit)`` — return recent briefs as BriefRecord list.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

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
