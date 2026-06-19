# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""m0005: Tenant-scope the graph content layer (Phase 1a — foundation).

Idempotent. Safe to re-run. NON-BREAKING — single-user deployments are
unaffected: every existing content node is stamped ``tenant_id="default"``
(``CERID_DEFAULT_TENANT``), which is exactly its current *implicit* scope.

Background (tasks/2026-06-18-tenant-scoping-memo.md):
the graph layer (``app/db/neo4j/*``) has no tenant-isolation boundary —
0/22 modules consult ``get_tenant_id()``, and content nodes carry no
``tenant_id`` property to filter on. That is a hard blocker for
``CERID_MULTI_USER=true`` (every tenant would read the union of all
tenants' knowledge). This migration lays the *foundation* of the memo's
§5 checklist step 1: it gives every content node a queryable
``tenant_id`` and an index to filter on, so the later Cypher
choke-point (step 3, a graph analogue of ``with_tenant_scope``) has a
field to bind against.

Phase split — what this migration does and DELIBERATELY does NOT do:

  * 1a (HERE): add ``tenant_id`` (default "default") to every per-tenant
    content label + a single-property index per label. Non-breaking;
    read/write code is unchanged and still tenant-blind (single-user is
    one tenant, so there is nothing to cross). This is pure scaffolding.

  * 1b (DEFERRED — needs a dedicated, breaking migration + write-path
    changes): rework content-addressed uniqueness so identical content
    from two tenants no longer COLLAPSES onto one node. Today
    ``Artifact.id`` / ``Artifact.content_hash`` / ``Entity.canonical_id``
    are GLOBALLY unique (schema.py), so two tenants ingesting the same
    document share one ``:Artifact``. Fixing that means a Community-
    Edition-safe derived key — e.g. ``uid = "{tenant_id}|{content_hash}"``
    with single-property uniqueness (composite / NODE KEY constraints are
    Enterprise-only; mirrors the m0004 ``:Fact.uid`` pattern) — plus
    updating every ``MERGE`` in the ingest write path to key on it. That
    is intentionally out of scope here because it touches content-
    addressing and dedup across the whole pipeline and must land with
    its write-path changes, not ahead of them.

Labels scoped (the per-tenant content graph). ``:Domain`` is excluded —
it is a fixed, global taxonomy (``config.DOMAINS``), shared across
tenants. ``:User`` / ``:Tenant`` are identity nodes, already tenant-
aware via their own keys. ``:Fact`` is included so the m0004 bi-temporal
scaffolding is tenant-ready when its writer lands.
"""
from __future__ import annotations

import logging

from config.features import DEFAULT_TENANT_ID

logger = logging.getLogger("ai-companion.migrations.m0005")

# Per-tenant content labels. Verified present in the write paths
# (app/db/neo4j/*). :Fact has no writer yet (m0004 scaffolding) but is
# scoped now so it is tenant-ready by construction.
_CONTENT_LABELS = (
    "Artifact",
    "Entity",
    "Community",
    "Memory",
    "PendingArtifact",
    "Tag",
    "SubCategory",
    "Fact",
)


def run(driver) -> dict[str, int]:
    """Apply the migration: a tenant_id index + a default-tenant backfill
    per content label. Returns counts of indexes created/verified and
    labels backfilled.

    Idempotency: indexes use ``IF NOT EXISTS``; backfills are guarded by
    ``WHERE n.tenant_id IS NULL`` so a re-run touches nothing already
    stamped.
    """
    indexes = 0
    backfills = 0
    with driver.session() as session:
        for label in _CONTENT_LABELS:
            session.run(
                f"CREATE INDEX {label.lower()}_tenant_idx IF NOT EXISTS "
                f"FOR (n:{label}) ON (n.tenant_id)"
            )
            indexes += 1
            session.run(
                f"MATCH (n:{label}) WHERE n.tenant_id IS NULL "
                "SET n.tenant_id = $default",
                default=DEFAULT_TENANT_ID,
            )
            backfills += 1

    logger.info(
        "m0005: %d tenant indexes created/verified, %d labels backfilled to tenant=%r",
        indexes,
        backfills,
        DEFAULT_TENANT_ID,
    )
    return {"tenant_indexes": indexes, "labels_backfilled": backfills}
