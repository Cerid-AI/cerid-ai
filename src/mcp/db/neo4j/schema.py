# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j schema initialization — constraints, indexes, seed data."""

from __future__ import annotations

import logging

import config
from utils.time import utcnow_iso

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

    logger.info(
        f"Neo4j schema initialized with {len(config.TAXONOMY)} domains, "
        f"{sum(len(v.get('sub_categories', [])) for v in config.TAXONOMY.values())} sub-categories"
    )
