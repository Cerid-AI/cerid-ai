# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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
NOT backfill existing artifacts — backfill is the separate one-shot
``scripts/backfill_source_nodes.py`` (CL-1 follow-up). That script is
dry-run by default (``--apply`` to write) and idempotent: it
reconstructs each pre-CL-1 artifact's ``source_id`` from its Chroma
chunk metadata (the id is never stored on the :Artifact node — only
``client_source`` is), MERGEs the ``(:Artifact)-[:FROM_SOURCE]->(:Source)``
edge for every id that resolves to a real :Source, then recomputes each
source's ``total_artifacts`` / ``total_chunks`` in one SET-from-graph pass
(``SET s.total_artifacts = count(a), s.total_chunks = sum(a.chunk_count)``)
so re-running never double-counts. Artifacts whose ``source_id`` cannot be
reconstructed are reported grouped by (client_source, source_type) rather
than linked — the script never fabricates :Source nodes.
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
