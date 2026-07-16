# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j data access for the (:Source) node — the canonical record of
every ingestion stream (folder, RSS, webhook, gmail, etc.).

See ``tasks/2026-05-24-ingestion-experience-plan.md`` §2 for the
shape rationale. The 21 valid `kind` values live in
``core.ingest.sources.kinds`` so the protocol layer and the Neo4j
layer share one source of truth.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("ai-companion.db.sources")


# ---------------------------------------------------------------------------
# Create / upsert
# ---------------------------------------------------------------------------


def create_source(
    driver,
    *,
    kind: str,
    family: str,
    display_name: str,
    config: dict[str, Any],
    tier: str = "core",
    quality_floor: float = 0.0,
    retention_policy: dict[str, Any] | None = None,
    connection_time_ms: int | None = None,
) -> dict[str, Any]:
    """Create a new Source node and return its full record.

    ``id`` is generated server-side; callers receive it for subsequent
    reference. ``config`` is JSON-serialized into the node property
    so per-kind schemas don't need to leak into Cypher.
    """
    source_id = str(uuid.uuid4())
    now = datetime.now(tz=timezone.utc).isoformat()
    # Retention is opt-in. Absent an explicit operator-supplied policy we default
    # to keep_all, which enforce_all_retention() deliberately skips — nothing is
    # ever purged until an operator configures a concrete dict policy
    # (mode != "keep_all"). This default is intentional, not a missing feature.
    retention = retention_policy or {"mode": "keep_all"}

    with driver.session() as session:
        record = session.run(
            """
            CREATE (s:Source {
              id: $id,
              kind: $kind,
              family: $family,
              display_name: $display_name,
              config: $config,
              tier: $tier,
              status: 'connected',
              sync_cursor: '{}',
              retention_policy: $retention,
              quality_floor: $quality_floor,
              connection_time_ms: $connection_time_ms,
              total_artifacts: 0,
              total_chunks: 0,
              total_edges: 0,
              total_artifacts_24h: 0,
              last_sync_at: $now,
              created_at: $now
            })
            RETURN s
            """,
            id=source_id,
            kind=kind,
            family=family,
            display_name=display_name,
            config=json.dumps(config),
            tier=tier,
            retention=json.dumps(retention),
            quality_floor=quality_floor,
            connection_time_ms=connection_time_ms,
            now=now,
        ).single()

    if record is None:
        raise RuntimeError(f"create_source did not return a record for kind={kind}")
    return _node_to_dict(record["s"])


def get_source(driver, source_id: str) -> dict[str, Any] | None:
    """Fetch a Source by id. Returns None when not found."""
    with driver.session() as session:
        record = session.run(
            "MATCH (s:Source {id: $id}) RETURN s",
            id=source_id,
        ).single()
    if record is None:
        return None
    return _node_to_dict(record["s"])


def list_sources(driver, *, kind: str | None = None) -> list[dict[str, Any]]:
    """List sources, newest first. Optionally filter by kind."""
    with driver.session() as session:
        if kind is None:
            rows = session.run(
                "MATCH (s:Source) RETURN s ORDER BY s.created_at DESC",
            )
        else:
            rows = session.run(
                "MATCH (s:Source {kind: $kind}) RETURN s ORDER BY s.created_at DESC",
                kind=kind,
            )
        return [_node_to_dict(r["s"]) for r in rows]


def update_source_cursor(driver, source_id: str, cursor: dict[str, Any]) -> None:
    """Persist the latest sync cursor. Idempotent; safe to call from any worker."""
    with driver.session() as session:
        session.run(
            """
            MATCH (s:Source {id: $id})
            SET s.sync_cursor = $cursor,
                s.last_sync_at = $now
            """,
            id=source_id,
            cursor=json.dumps(cursor),
            now=datetime.now(tz=timezone.utc).isoformat(),
        )


def update_source_status(
    driver,
    source_id: str,
    *,
    status: str,
    last_error: str | None = None,
) -> None:
    """Update connection status. ``status`` ∈ connected | error | paused | needs_auth."""
    with driver.session() as session:
        session.run(
            """
            MATCH (s:Source {id: $id})
            SET s.status = $status,
                s.last_error = $last_error
            """,
            id=source_id,
            status=status,
            last_error=last_error,
        )


def increment_source_counters(
    driver,
    source_id: str,
    *,
    artifacts: int = 0,
    chunks: int = 0,
    edges: int = 0,
) -> None:
    """Apply a delta to the running counters. Atomic Cypher SET +=."""
    with driver.session() as session:
        session.run(
            """
            MATCH (s:Source {id: $id})
            SET s.total_artifacts = coalesce(s.total_artifacts, 0) + $artifacts,
                s.total_chunks = coalesce(s.total_chunks, 0) + $chunks,
                s.total_edges = coalesce(s.total_edges, 0) + $edges,
                s.total_artifacts_24h = coalesce(s.total_artifacts_24h, 0) + $artifacts
            """,
            id=source_id,
            artifacts=artifacts,
            chunks=chunks,
            edges=edges,
        )


def delete_source(driver, source_id: str, *, cascade: bool = False) -> None:
    """Remove a Source. ``cascade=True`` also detaches FROM_SOURCE edges
    (artifacts themselves survive). Tombstone-style — the artifact node
    isn't destroyed; only its source linkage."""
    with driver.session() as session:
        if cascade:
            session.run(
                """
                MATCH (a:Artifact)-[r:FROM_SOURCE]->(s:Source {id: $id})
                DELETE r
                """,
                id=source_id,
            )
        session.run(
            "MATCH (s:Source {id: $id}) DETACH DELETE s",
            id=source_id,
        )


def update_source_config(
    driver,
    source_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Persist an updated config dict for a Source. Returns the refreshed record.

    Called from ``POST /sources/{id}/config`` after the connector re-validates
    the merged config. The caller is responsible for merging and dropping any
    redaction-mask values before passing ``config`` here.
    """
    with driver.session() as session:
        session.run(
            """
            MATCH (s:Source {id: $id})
            SET s.config = $config
            """,
            id=source_id,
            config=json.dumps(config),
        )
    refreshed = get_source(driver, source_id)
    if refreshed is None:
        raise RuntimeError(f"update_source_config: source {source_id!r} disappeared after write")
    return refreshed


def link_artifact(driver, artifact_id: str, source_id: str) -> None:
    """Create the (:Artifact)-[:FROM_SOURCE]->(:Source) edge. Idempotent."""
    with driver.session() as session:
        session.run(
            """
            MATCH (a:Artifact {id: $artifact_id}), (s:Source {id: $source_id})
            MERGE (a)-[:FROM_SOURCE]->(s)
            """,
            artifact_id=artifact_id,
            source_id=source_id,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node_to_dict(node: Any) -> dict[str, Any]:
    """Convert a Neo4j Node into a plain dict, deserializing JSON-encoded
    properties (``config``, ``sync_cursor``, ``retention_policy``)."""
    data = dict(node)
    for key in ("config", "sync_cursor", "retention_policy"):
        raw = data.get(key)
        if isinstance(raw, str):
            try:
                data[key] = json.loads(raw)
            except json.JSONDecodeError:
                # Defensive: leave the raw string in place so the caller
                # can see what went wrong rather than swallowing it.
                data[key] = {"_parse_error": raw[:200]}
    return data
