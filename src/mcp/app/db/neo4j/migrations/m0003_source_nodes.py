# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""m0003: Introduce (:Source) nodes + (:Artifact)-[:FROM_SOURCE]->(:Source) edges.

Idempotent. Safe to re-run.

Pre-2026-05 the system tagged Artifact nodes with `client_source` /
`source_type` strings. The Ingestion Experience program
(tasks/2026-05-24-ingestion-experience-plan.md §2) introduces an
explicit Source node so:

  1. Every artifact links back to its origin via a typed edge.
  2. The FE can render per-source state (sync cursor, retention
     policy, quality floor, connection time) without joining across
     Artifact records.
  3. Future capture clients (mobile, browser-ext) get a stable
     identifier per ingestion stream.

This migration creates two unique constraints + an index. It does
NOT backfill existing artifacts — backfill is a separate one-shot
script (scripts/backfill_source_nodes.py, Phase 1 follow-up) that
groups artifacts by (client_source, source_type) and creates a
Source node per distinct combination.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("ai-companion.migrations.m0003")

# Source IDs are UUIDs assigned at creation time. Unique constraint
# guards against double-create on retry; the application-level
# upsert uses MERGE on this property.
_SOURCE_ID_CONSTRAINT = """
CREATE CONSTRAINT source_id_unique IF NOT EXISTS
FOR (s:Source) REQUIRE s.id IS UNIQUE
"""

# Display-name index speeds the FE's source-list query which sorts
# alphabetically by default. Not unique — two webhooks named "Slack
# capture" should coexist.
_SOURCE_NAME_INDEX = """
CREATE INDEX source_display_name_idx IF NOT EXISTS
FOR (s:Source) ON (s.display_name)
"""

# Kind index speeds the radial-FAB grouping query and the diversity-
# bar Cypher.
_SOURCE_KIND_INDEX = """
CREATE INDEX source_kind_idx IF NOT EXISTS
FOR (s:Source) ON (s.kind)
"""


def run(driver) -> dict[str, int]:
    """Apply the migration. Returns counts of constraints + indexes
    created or already-present (Neo4j IF NOT EXISTS makes both
    paths look identical from the client side)."""
    created = 0
    with driver.session() as session:
        for cypher in (_SOURCE_ID_CONSTRAINT, _SOURCE_NAME_INDEX, _SOURCE_KIND_INDEX):
            session.run(cypher)
            created += 1
    logger.info("m0003: created/verified %d Source schema objects", created)
    return {"schema_objects": created}
