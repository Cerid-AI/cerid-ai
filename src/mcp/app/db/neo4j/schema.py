# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j schema initialization — constraints, indexes, seed data."""

from __future__ import annotations

import logging

import config
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.graph")


def init_schema(driver) -> None:
    """Create constraints, indexes, and seed Domain/SubCategory nodes. Idempotent."""
    with driver.session() as session:
        # --- Core constraints ---
        session.run(
            "CREATE CONSTRAINT artifact_id IF NOT EXISTS "
            "FOR (a:Artifact) REQUIRE a.id IS UNIQUE"
        )
        session.run(
            "CREATE CONSTRAINT domain_name IF NOT EXISTS "
            "FOR (d:Domain) REQUIRE d.name IS UNIQUE"
        )
        # Drop the old index if it exists (being replaced by unique constraint)
        try:
            session.run("DROP INDEX artifact_content_hash IF EXISTS")
        except Exception as e:
            logger.debug(f"Old index drop skipped: {e}")
        session.run(
            "CREATE CONSTRAINT artifact_content_hash_unique IF NOT EXISTS "
            "FOR (a:Artifact) REQUIRE a.content_hash IS UNIQUE"
        )

        # --- SubCategory + Tag constraints ---
        session.run(
            "CREATE CONSTRAINT subcategory_name IF NOT EXISTS "
            "FOR (sc:SubCategory) REQUIRE sc.name IS UNIQUE"
        )
        session.run(
            "CREATE CONSTRAINT tag_name IF NOT EXISTS "
            "FOR (t:Tag) REQUIRE t.name IS UNIQUE"
        )

        # --- Multi-user constraints (always created, only populated when enabled) ---
        session.run(
            "CREATE CONSTRAINT user_id IF NOT EXISTS "
            "FOR (u:User) REQUIRE u.id IS UNIQUE"
        )
        session.run(
            "CREATE CONSTRAINT user_email IF NOT EXISTS "
            "FOR (u:User) REQUIRE u.email IS UNIQUE"
        )
        session.run(
            "CREATE CONSTRAINT tenant_id IF NOT EXISTS "
            "FOR (t:Tenant) REQUIRE t.id IS UNIQUE"
        )

        # --- Indexes ---
        session.run(
            "CREATE INDEX artifact_domain_idx IF NOT EXISTS "
            "FOR (a:Artifact) ON (a.domain)"
        )
        session.run(
            "CREATE INDEX artifact_filename_idx IF NOT EXISTS "
            "FOR (a:Artifact) ON (a.filename)"
        )
        session.run(
            "CREATE INDEX artifact_sub_category_idx IF NOT EXISTS "
            "FOR (a:Artifact) ON (a.sub_category)"
        )
        session.run(
            "CREATE INDEX artifact_quality_idx IF NOT EXISTS "
            "FOR (a:Artifact) ON (a.quality_score)"
        )
        session.run(
            "CREATE INDEX artifact_updated_at_idx IF NOT EXISTS "
            "FOR (a:Artifact) ON (a.updated_at)"
        )

        # --- Entity layer (Workstream E Phase 4a.2) ---
        # GraphRAG entity nodes that materialise from LLM-extracted mentions
        # over chunk text. The (:Artifact)-[:MENTIONS]->(:Entity) shape is
        # artifact-level, not chunk-level — a deviation from the
        # tasks/2026-04-28-workstream-e-rag-modernization.md plan that
        # avoids proliferating ~13K Chunk nodes into Neo4j. The
        # ChromaNeo4jRetriever (Phase 4a.5) does not need Neo4j-side Chunk
        # nodes: vector search returns chunk_ids → MATCH against Artifact
        # → expansion via MENTIONS edges. Chunk-grain mentions ride on the
        # MENTIONS edge as a `chunk_ids` property (JSON-encoded list, same
        # convention as keywords_json).
        session.run(
            "CREATE CONSTRAINT entity_canonical_id IF NOT EXISTS "
            "FOR (e:Entity) REQUIRE e.canonical_id IS UNIQUE"
        )
        session.run(
            "CREATE INDEX entity_name_idx IF NOT EXISTS "
            "FOR (e:Entity) ON (e.name)"
        )
        session.run(
            "CREATE INDEX entity_type_idx IF NOT EXISTS "
            "FOR (e:Entity) ON (e.entity_type)"
        )

        # --- Community layer (Workstream E Phase 4b.1) ---
        # Leiden community detection materialises (:Community) nodes
        # over the Entity graph; each Entity gets one or more
        # [:IN_COMMUNITY] edges (one per hierarchical level Leiden
        # produces). Community.id is "{level}:{native_id}" so multiple
        # levels can coexist without collision.
        session.run(
            "CREATE CONSTRAINT community_id IF NOT EXISTS "
            "FOR (c:Community) REQUIRE c.id IS UNIQUE"
        )
        session.run(
            "CREATE INDEX community_level_idx IF NOT EXISTS "
            "FOR (c:Community) ON (c.level)"
        )

        # --- Wikilink layer (RAG Cycle C2.1) ---
        # PendingArtifact placeholders preserve broken [[wikilink]] targets
        # until the real artifact lands; resolve_pending_artifacts re-points
        # the inbound WIKILINKS_TO / EMBEDS edges and deletes the placeholder.
        session.run(
            "CREATE CONSTRAINT pending_artifact_name IF NOT EXISTS "
            "FOR (p:PendingArtifact) REQUIRE p.name IS UNIQUE"
        )

        # --- Active learning layer (v0.95 Phase 5) ---
        # The :RATED edge type powers trust_score.user_agreement and the
        # pkb_rate tool. The :Correction node carries user-submitted
        # corrections linked to specific artifacts (and optionally
        # specific chunks). endorsement_weight / flag_reason properties
        # ride on :Artifact so the reranker can read them without an
        # extra hop.
        #
        # Existence guarantee: even with zero ratings/corrections this
        # block runs cleanly (constraints don't require rows). That
        # turns trust_score.user_agreement from "permanently
        # not_available" (no :RATED type) into "available with zero
        # ratings" — a meaningful semantic shift handled at the
        # trust_score reader level.
        session.run(
            "CREATE CONSTRAINT correction_id IF NOT EXISTS "
            "FOR (c:Correction) REQUIRE c.id IS UNIQUE"
        )
        session.run(
            "CREATE INDEX correction_artifact_id_idx IF NOT EXISTS "
            "FOR (c:Correction) ON (c.artifact_id)"
        )
        session.run(
            "CREATE INDEX correction_ts_idx IF NOT EXISTS "
            "FOR (c:Correction) ON (c.ts)"
        )
        # Backfill default endorsement_weight on any :Artifact that
        # doesn't have one yet. Idempotent — no-op once everything is
        # 1.0. Future ingests inherit the default at the node level
        # via the ingest path setting it explicitly.
        session.run(
            "MATCH (a:Artifact) WHERE a.endorsement_weight IS NULL "
            "SET a.endorsement_weight = 1.0"
        )

        # --- Seed Domain + SubCategory nodes ---
        now = utcnow_iso()
        for domain_name, domain_info in config.TAXONOMY.items():
            session.run(
                "MERGE (d:Domain {name: $name}) "
                "ON CREATE SET d.description = $desc, d.icon = $icon, d.created_at = $now",
                name=domain_name,
                desc=domain_info.get("description", ""),
                icon=domain_info.get("icon", "file"),
                now=now,
            )
            for sub_cat in domain_info.get("sub_categories", ["general"]):
                # SubCategory.name is globally unique: "domain/sub_category"
                sc_name = f"{domain_name}/{sub_cat}"
                session.run(
                    "MERGE (sc:SubCategory {name: $sc_name}) "
                    "ON CREATE SET sc.domain = $domain, sc.label = $label, sc.created_at = $now "
                    "WITH sc "
                    "MATCH (d:Domain {name: $domain}) "
                    "MERGE (sc)-[:BELONGS_TO]->(d)",
                    sc_name=sc_name,
                    domain=domain_name,
                    label=sub_cat,
                    now=now,
                )

        # --- Repair: backfill missing sub_category + CATEGORIZED_AS ---
        fixed = session.run(
            "MATCH (a:Artifact) WHERE a.sub_category IS NULL "
            "SET a.sub_category = $default RETURN count(a) AS n",
            default=config.DEFAULT_SUB_CATEGORY,
        ).single()["n"]

        linked = session.run(
            "MATCH (a:Artifact)-[:BELONGS_TO]->(d:Domain) "
            "WHERE NOT (a)-[:CATEGORIZED_AS]->() "
            "WITH a, d, coalesce(a.sub_category, $default) AS sc "
            "MATCH (subcat:SubCategory {name: d.name + '/' + sc}) "
            "MERGE (a)-[:CATEGORIZED_AS]->(subcat) "
            "RETURN count(a) AS n",
            default=config.DEFAULT_SUB_CATEGORY,
        ).single()["n"]

        if fixed or linked:
            logger.info(f"Backfilled {fixed} sub_category props, {linked} CATEGORIZED_AS rels")

    # --- Memory node schema (Phase 44 Part 2) ---
    from app.db.neo4j.memory import ensure_memory_schema
    ensure_memory_schema(driver)

    logger.info(
        f"Neo4j schema initialized with {len(config.TAXONOMY)} domains, "
        f"{sum(len(v.get('sub_categories', [])) for v in config.TAXONOMY.values())} sub-categories"
    )
