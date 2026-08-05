# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Neo4j persistence for the contradiction ledger (W.4).

Schema (matches the W.4 plan):

    (:ContradictionFinding {
        finding_id, detected_at, severity, query_ctx_id,
        claim_a_text, claim_b_text, entity_slug
    })

    (:ContradictionFinding)-[:DERIVES_FROM]->(:Claim)   // two per finding
    (:Claim)-[:CONTRADICTS {detected_at, severity}]->(:Claim)  // for graph queries

The ContradictionFinding node provides a denormalized summary record
suitable for the wiki contradiction list. The pair of DERIVES_FROM edges
links back to the individual :Claim nodes so future graph traversals can
follow the provenance chain. The direct :CONTRADICTS edge between Claim
nodes enables fast graph queries (e.g. «find all contradictions involving
entity X») without scanning every ContradictionFinding.

Callers: :mod:`app.services.contradiction_log` only.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from core.utils.swallowed import log_swallowed_error

if TYPE_CHECKING:
    from app.services.contradiction_log import ContradictionFinding

logger = logging.getLogger("ai-companion.graph.contradictions")


def record_contradiction(driver: Any, finding: "ContradictionFinding") -> str:
    """Persist a :class:`~app.services.contradiction_log.ContradictionFinding`.

    Creates:
    - A ``(:ContradictionFinding)`` node keyed by ``finding_id``
    - Two ``(:Claim)`` nodes (MERGE on ``claim_id``)
    - Two ``(:ContradictionFinding)-[:DERIVES_FROM]->(:Claim)`` edges
    - One ``(:Claim)-[:CONTRADICTS]->(:Claim)`` edge with ``detected_at`` +
      ``severity`` properties

    All writes happen in a single transaction for atomicity.

    Returns ``finding_id`` on success so the caller can surface it in the
    API response without re-reading from the database.
    """
    source_artifacts_str = ",".join(finding.source_artifacts)

    with driver.session() as session:
        try:
            session.run(
                """
                // 1. Create or update the ContradictionFinding node
                MERGE (f:ContradictionFinding {finding_id: $finding_id})
                  ON CREATE SET
                    f.detected_at    = $detected_at,
                    f.severity       = $severity,
                    f.query_ctx_id   = $query_ctx_id,
                    f.entity_slug    = $entity_slug,
                    f.claim_a_text   = $claim_a_text,
                    f.claim_b_text   = $claim_b_text,
                    f.source_artifacts = $source_artifacts
                  ON MATCH SET
                    f.severity         = $severity,
                    f.source_artifacts = $source_artifacts

                // 2. Ensure both Claim nodes exist
                MERGE (ca:Claim {claim_id: $claim_a_id})
                  ON CREATE SET ca.text = $claim_a_text, ca.created_at = $detected_at
                MERGE (cb:Claim {claim_id: $claim_b_id})
                  ON CREATE SET cb.text = $claim_b_text, cb.created_at = $detected_at

                // 3. DERIVES_FROM edges from finding → each claim
                MERGE (f)-[:DERIVES_FROM]->(ca)
                MERGE (f)-[:DERIVES_FROM]->(cb)

                // 4. Direct CONTRADICTS edge for graph queries
                MERGE (ca)-[c:CONTRADICTS]->(cb)
                  ON CREATE SET c.detected_at = $detected_at, c.severity = $severity
                  ON MATCH  SET c.detected_at = $detected_at, c.severity = $severity

                // 5. Phase K2.3 — typed edge from the focal entity to
                // the finding, so graph traversals + wiki rendering
                // don't have to property-filter on every Contradiction.
                // Only runs when an entity_slug was attached; the
                // OPTIONAL MATCH keeps the txn alive when the slug is
                // unknown (the entity may not exist yet).
                WITH f
                OPTIONAL MATCH (e:Entity {canonical_id: $entity_slug})
                FOREACH (_ IN CASE WHEN e IS NULL OR $entity_slug = "" THEN [] ELSE [1] END |
                    MERGE (e)-[r:HAS_CONTRADICTION]->(f)
                      ON CREATE SET r.linked_at = $detected_at
                )
                """,
                finding_id=finding.finding_id,
                detected_at=finding.detected_at,
                severity=finding.severity,
                query_ctx_id=finding.query_ctx_id or "",
                entity_slug=finding.entity_slug or "",
                claim_a_id=finding.claim_a_id,
                claim_b_id=finding.claim_b_id,
                claim_a_text=finding.claim_a_text,
                claim_b_text=finding.claim_b_text,
                source_artifacts=source_artifacts_str,
            )
        except Exception as exc:
            log_swallowed_error("contradiction_log", exc, context={"finding_id": finding.finding_id})
            raise

    return finding.finding_id


def list_contradictions(
    driver: Any,
    *,
    entity_slug: str | None = None,
    since: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return a list of ContradictionFinding rows as raw dicts.

    Parameters
    ----------
    entity_slug:
        When provided, filters to findings whose ``entity_slug`` matches.
    since:
        ISO-8601 timestamp string (inclusive lower bound on ``detected_at``).
    limit:
        Maximum rows returned. Capped at 1000 to prevent runaway queries.
    """
    effective_limit = min(limit, 1000)

    parts = ["MATCH (f:ContradictionFinding)"]
    conditions: list[str] = []
    params: dict[str, Any] = {"limit": effective_limit}

    if entity_slug:
        conditions.append("f.entity_slug = $entity_slug")
        params["entity_slug"] = entity_slug

    if since:
        conditions.append("f.detected_at >= $since")
        params["since"] = since

    if conditions:
        parts.append("WHERE " + " AND ".join(conditions))

    parts.append("RETURN f ORDER BY f.detected_at DESC LIMIT $limit")
    cypher = "\n".join(parts)

    rows: list[dict[str, Any]] = []
    try:
        with driver.session() as session:
            result = session.run(cypher, **params)
            for record in result:
                rows.append(dict(record["f"]))
    except Exception as exc:
        log_swallowed_error("contradiction_log", exc, context={"entity_slug": entity_slug})
        raise

    return rows


def get_contradiction(driver: Any, finding_id: str) -> dict[str, Any] | None:
    """Fetch a single ContradictionFinding by ``finding_id``.

    Returns ``None`` when no matching node exists.
    """
    try:
        with driver.session() as session:
            result = session.run(
                "MATCH (f:ContradictionFinding {finding_id: $finding_id}) RETURN f",
                finding_id=finding_id,
            )
            record = result.single()
            if record is None:
                return None
            return dict(record["f"])
    except Exception as exc:
        log_swallowed_error("contradiction_log", exc, context={"finding_id": finding_id})
        raise
