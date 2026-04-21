# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j schema migrations — one-time, idempotent transformations.

Previously lived at ``app/db/neo4j/migrations.py``; moved here when
``migrations`` became a package to host versioned migrations.
"""

from __future__ import annotations

import logging
from typing import Any

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


def register_recategorized_at(driver) -> dict[str, Any]:
    """Register ``recategorized_at`` property key in Neo4j schema.

    Creates an index so Neo4j recognizes the property key, silencing
    the UnknownPropertyKeyWarning emitted by the rectify agent's
    stale-artifact query on installations where no artifact has been
    recategorized yet.  Idempotent (IF NOT EXISTS).
    """
    with driver.session() as session:
        session.run(
            "CREATE INDEX artifact_recategorized_at_idx IF NOT EXISTS "
            "FOR (a:Artifact) ON (a.recategorized_at)"
        )
    logger.info("Migration: registered recategorized_at index")
    return {"status": "index_created"}


def migrate_memory_salience(driver) -> dict[str, Any]:
    """
    Migrate Memory nodes from legacy ``memory_type`` values to the current
    ``MEMORY_TYPES`` enum defined in ``config/settings.py``.

    Maps old types using ``MEMORY_TYPE_MIGRATION`` (e.g. ``fact`` -> ``empirical``).
    Idempotent: skips nodes that already have a valid type.
    """
    from config.settings import MEMORY_TYPE_MIGRATION, MEMORY_TYPES

    with driver.session() as session:
        # Find Memory/Artifact nodes whose memory_type is NOT in the current enum
        valid_types = list(MEMORY_TYPES)
        records = list(
            session.run(
                "MATCH (a:Artifact) WHERE a.memory_type IS NOT NULL "
                "AND NOT a.memory_type IN $valid_types "
                "RETURN a.content_hash AS id, a.memory_type AS memory_type",
                valid_types=valid_types,
            )
        )

        migrated = 0
        for record in records:
            node_id = record["id"]
            old_type = record["memory_type"]
            new_type = MEMORY_TYPE_MIGRATION.get(old_type, old_type)
            if new_type in MEMORY_TYPES:
                session.run(
                    "MATCH (a:Artifact {content_hash: $id}) "
                    "SET a.memory_type = $mem_type",
                    id=node_id,
                    mem_type=new_type,
                )
                migrated += 1

    if migrated:
        logger.info("Migration: migrated memory_type on %d artifacts", migrated)
    return {"migrated": migrated}
