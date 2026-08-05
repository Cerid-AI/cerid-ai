# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Per-source retention enforcement — app-layer driver.

Calls into :mod:`core.ingest.retention` for the policy logic, then
applies the resulting purge plans via the content-lifecycle coordinator
(:func:`app.services.content_lifecycle.remove_content`), which hard-deletes
each artifact across Neo4j + Chroma + BM25 + SPLADE and busts the query
caches. Triggered nightly by the scheduler (``SCHEDULE_RETENTION_ENFORCE``).
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.neo4j import sources as srcdb
from app.deps import get_neo4j
from core.ingest.retention import ArtifactRef, RetentionDecision, plan_for_source
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.services.retention")


def _fetch_artifacts_for_source(driver, source_id: str) -> list[ArtifactRef]:
    """Return the source's artifacts newest-first, as ArtifactRefs.

    Queries Neo4j directly via the (:Artifact)-[:FROM_SOURCE]->(:Source)
    edge. Chroma stays untouched until the purge step.
    """
    with driver.session() as session:
        rows = session.run(
            """
            MATCH (a:Artifact)-[:FROM_SOURCE]->(s:Source {id: $sid})
            RETURN a.id AS id, a.created_at AS created_at
            ORDER BY a.created_at DESC
            """,
            sid=source_id,
        )
        return [
            ArtifactRef(
                artifact_id=r["id"],
                source_id=source_id,
                created_at=r.get("created_at") or "",
            )
            for r in rows
            if r.get("id")
        ]


def apply_retention_plan(driver, decision: RetentionDecision) -> int:
    """Purge artifacts named in the decision. Returns the count
    actually purged. Atomic per-artifact; partial failures are
    logged via swallowed_error and don't roll back the rest.
    """
    if not decision.purge:
        return 0

    from app.services.content_lifecycle import remove_content

    purged = 0
    for artifact_id in decision.purge:
        try:
            # HARD delete through the content-lifecycle coordinator: drops the
            # Neo4j node (source of chunk_ids) and fans chunk removal across
            # Chroma + BM25 + SPLADE, then busts the query caches. Per-artifact
            # try/except keeps a single failure from rolling back the rest.
            result = remove_content(artifact_id, neo4j=driver)
            if result.found:
                purged += 1
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("retention.apply_one", exc, context={"artifact_id": artifact_id})

    return purged


def enforce_all_retention() -> dict[str, Any]:
    """Walk every Source, compute its retention plan, and apply it.

    Returns a summary dict for the operator endpoint.
    """
    driver = get_neo4j()
    sources = srcdb.list_sources(driver)
    total_purged = 0
    per_source: list[dict[str, Any]] = []

    for src in sources:
        # Retention is opt-in: only sources with an explicit dict policy whose
        # mode is not "keep_all" are ever enforced. keep_all is the intentional
        # default set by create_source(), so an operator must deliberately
        # configure a concrete policy before any artifact is purged. Non-dict /
        # malformed policies are skipped for the same fail-safe reason.
        policy = src.get("retention_policy") or {}
        if not isinstance(policy, dict):
            continue
        if policy.get("mode") == "keep_all":
            continue

        artifacts = _fetch_artifacts_for_source(driver, src["id"])
        decision = plan_for_source(src["id"], policy, artifacts)
        if not decision.purge:
            continue

        purged = apply_retention_plan(driver, decision)
        total_purged += purged
        per_source.append({
            "source_id": src["id"],
            "kind": src["kind"],
            "purged": purged,
            "kept": decision.keep_count,
        })

    logger.info(
        "Retention pass complete: %d artifacts purged across %d sources",
        total_purged,
        len(per_source),
    )
    return {
        "total_purged": total_purged,
        "sources_affected": len(per_source),
        "per_source": per_source,
    }
