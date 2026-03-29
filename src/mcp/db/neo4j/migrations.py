# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j schema migrations — one-time, idempotent transformations."""

from __future__ import annotations

import logging
from typing import Any

import config

logger = logging.getLogger("ai-companion.graph")


def backfill_updated_at(driver) -> dict[str, Any]:
    """
    Backfill ``updated_at`` on Artifact nodes that lack it.

    Sets ``updated_at = coalesce(modified_at, recategorized_at, ingested_at)``
    so that incremental sync can filter on a single field.
    Safe to re-run (guarded by ``WHERE a.updated_at IS NULL``).
    """
    with driver.session() as session:
        result = session.run(
            "MATCH (a:Artifact) WHERE a.updated_at IS NULL "
            "SET a.updated_at = coalesce(a.modified_at, a.recategorized_at, a.ingested_at) "
            "RETURN count(a) AS backfilled"
        )
        backfilled = result.single()["backfilled"]

        # Create index for fast incremental exports
        session.run(
            "CREATE INDEX artifact_updated_at_idx IF NOT EXISTS "
            "FOR (a:Artifact) ON (a.updated_at)"
        )

    if backfilled:
        logger.info("Migration: backfilled updated_at on %d artifacts", backfilled)
    return {"backfilled": backfilled}


def migrate_memory_salience(driver) -> dict[str, Any]:
    """Phase 51: Backfill memory salience fields on conversation Artifact nodes.

    Idempotent — guarded by ``WHERE a.source_authority IS NULL``.

    Updates:
    - memory_type: maps legacy ``fact`` → ``empirical``, ``action_item`` → ``project_context``
    - stability_days: per-type default from config.MEMORY_TYPE_STABILITY
    - source_authority: defaults to config.DEFAULT_SOURCE_AUTHORITY (0.7)
    - access_log: initialised to empty list if absent
    """
    type_migration = config.MEMORY_TYPE_MIGRATION
    stability_map = config.MEMORY_TYPE_STABILITY
    default_authority = config.DEFAULT_SOURCE_AUTHORITY

    with driver.session() as session:
        # Fetch conversation-domain artifacts that lack source_authority (unmigrated)
        result = session.run(
            "MATCH (a:Artifact)-[:BELONGS_TO]->(:Domain {name: 'conversations'}) "
            "WHERE a.source_authority IS NULL "
            "RETURN a.id AS id, a.memory_type AS memory_type"
        )
        records = list(result)

    migrated = 0
    for record in records:
        art_id = record["id"]
        raw_type = record["memory_type"] or "empirical"

        # Map legacy types
        new_type = type_migration.get(raw_type, raw_type)
        if new_type not in config.MEMORY_TYPES:
            new_type = "empirical"

        stability = stability_map.get(new_type, 30.0)
        # Use 999999.0 as sentinel for infinite stability (matches ChromaDB's "inf" string)
        stability_val = 999999.0 if stability == float("inf") else stability

        with driver.session() as session:
            session.run(
                "MATCH (a:Artifact {id: $aid}) "
                "SET a.memory_type = $mem_type, "
                "    a.stability_days = $stability, "
                "    a.source_authority = $authority, "
                "    a.access_log = coalesce(a.access_log, [])",
                aid=art_id,
                mem_type=new_type,
                stability=stability_val,
                authority=default_authority,
            )
        migrated += 1

    if migrated:
        logger.info("Phase 51 migration: updated %d memory artifacts", migrated)
    return {"migrated": migrated}
