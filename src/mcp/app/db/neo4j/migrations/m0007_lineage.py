# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""m0007: indexes for lineages (forget engine phase 5, spec §7).

Idempotent, schema only. Every version of a memory carries ``lineage_id``, and
the lineage writer finds a lineage's versions by it; a fact is one version per
source memory and is found by ``source_artifact_id``. The data itself is moved
by ``app/services/lineage_migration.py``, which runs at boot and calls this
first.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("ai-companion.migrations.m0007")

_INDEXES = (
    "CREATE INDEX artifact_lineage_idx IF NOT EXISTS FOR (a:Artifact) ON (a.lineage_id)",
    "CREATE INDEX memory_lineage_idx IF NOT EXISTS FOR (m:Memory) ON (m.lineage_id)",
    "CREATE INDEX artifact_superseded_by_idx IF NOT EXISTS FOR (a:Artifact) ON (a.superseded_by)",
    "CREATE INDEX fact_source_artifact_idx IF NOT EXISTS FOR (f:Fact) ON (f.source_artifact_id)",
)


def run(driver) -> dict[str, int]:
    with driver.session() as session:
        for cypher in _INDEXES:
            session.run(cypher)
    logger.info("m0007: created/verified %d lineage indexes", len(_INDEXES))
    return {"schema_objects": len(_INDEXES)}
