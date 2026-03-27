# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j schema migrations — one-time, idempotent transformations."""

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
