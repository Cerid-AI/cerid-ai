#!/usr/bin/env python3
"""
Cerid AI — Taxonomy Migration Script (Phase 8C)

Migrates existing artifacts from flat domains to hierarchical taxonomy.

What it does:
1. Creates SubCategory and Tag node types in Neo4j (idempotent)
2. Seeds sub-category nodes from TAXONOMY config
3. Sets sub_category='general' on all existing artifacts that lack one
4. Creates CATEGORIZED_AS relationships for existing artifacts
5. Reports migration statistics

This script is safe to run multiple times (idempotent).

Usage:
    python3 scripts/migrate_taxonomy.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

# Add src/mcp to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "mcp"))

import config  # noqa: E402


def get_driver():
    """Create a Neo4j driver from config."""
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

    from neo4j import GraphDatabase

    uri = os.getenv("NEO4J_URI", config.NEO4J_URI)
    user = os.getenv("NEO4J_USER", config.NEO4J_USER)
    password = os.getenv("NEO4J_PASSWORD", config.NEO4J_PASSWORD)

    return GraphDatabase.driver(uri, auth=(user, password))


def migrate(driver, dry_run: bool = False):
    """Run the taxonomy migration."""
    now = datetime.utcnow().isoformat()
    stats = {
        "domains_created": 0,
        "subcategories_created": 0,
        "artifacts_updated": 0,
        "categorized_as_created": 0,
    }

    with driver.session() as session:
        # Step 1: Create constraints (idempotent)
        print("[1/5] Creating constraints...")
        if not dry_run:
            session.run(
                "CREATE CONSTRAINT subcategory_name IF NOT EXISTS "
                "FOR (sc:SubCategory) REQUIRE sc.name IS UNIQUE"
            )
            session.run(
                "CREATE CONSTRAINT tag_name IF NOT EXISTS "
                "FOR (t:Tag) REQUIRE t.name IS UNIQUE"
            )
            session.run(
                "CREATE INDEX artifact_sub_category_idx IF NOT EXISTS "
                "FOR (a:Artifact) ON (a.sub_category)"
            )
        print("  ✓ Constraints ready")

        # Step 2: Seed Domain nodes with description/icon
        print("[2/5] Updating Domain nodes with taxonomy metadata...")
        for domain_name, domain_info in config.TAXONOMY.items():
            if not dry_run:
                result = session.run(
                    "MERGE (d:Domain {name: $name}) "
                    "ON CREATE SET d.description = $desc, d.icon = $icon, d.created_at = $now "
                    "ON MATCH SET d.description = $desc, d.icon = $icon "
                    "RETURN d.name AS name",
                    name=domain_name,
                    desc=domain_info.get("description", ""),
                    icon=domain_info.get("icon", "file"),
                    now=now,
                )
                if result.single():
                    stats["domains_created"] += 1
            else:
                stats["domains_created"] += 1
            print(f"  ✓ Domain: {domain_name}")

        # Step 3: Create SubCategory nodes
        print("[3/5] Creating SubCategory nodes...")
        for domain_name, domain_info in config.TAXONOMY.items():
            for sub_cat in domain_info.get("sub_categories", ["general"]):
                sc_name = f"{domain_name}/{sub_cat}"
                if not dry_run:
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
                stats["subcategories_created"] += 1
                print(f"  ✓ {sc_name}")

        # Step 4: Set default sub_category on existing artifacts
        print("[4/5] Setting default sub_category on existing artifacts...")
        if not dry_run:
            result = session.run(
                "MATCH (a:Artifact) "
                "WHERE a.sub_category IS NULL OR a.sub_category = '' "
                "SET a.sub_category = 'general' "
                "RETURN count(a) AS updated"
            )
            record = result.single()
            stats["artifacts_updated"] = record["updated"] if record else 0

            # Also set default tags to empty JSON array
            session.run(
                "MATCH (a:Artifact) "
                "WHERE a.tags IS NULL "
                "SET a.tags = '[]'"
            )
        else:
            result = session.run(
                "MATCH (a:Artifact) "
                "WHERE a.sub_category IS NULL OR a.sub_category = '' "
                "RETURN count(a) AS count"
            )
            record = result.single()
            stats["artifacts_updated"] = record["count"] if record else 0
        print(f"  ✓ {stats['artifacts_updated']} artifacts updated")

        # Step 5: Create CATEGORIZED_AS relationships
        print("[5/5] Creating CATEGORIZED_AS relationships...")
        if not dry_run:
            result = session.run(
                "MATCH (a:Artifact) "
                "WHERE NOT (a)-[:CATEGORIZED_AS]->() "
                "WITH a, a.domain + '/' + COALESCE(a.sub_category, 'general') AS sc_name "
                "MATCH (sc:SubCategory {name: sc_name}) "
                "CREATE (a)-[:CATEGORIZED_AS]->(sc) "
                "RETURN count(a) AS linked"
            )
            record = result.single()
            stats["categorized_as_created"] = record["linked"] if record else 0
        else:
            result = session.run(
                "MATCH (a:Artifact) "
                "WHERE NOT (a)-[:CATEGORIZED_AS]->() "
                "RETURN count(a) AS count"
            )
            record = result.single()
            stats["categorized_as_created"] = record["count"] if record else 0
        print(f"  ✓ {stats['categorized_as_created']} relationships created")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Migrate to hierarchical taxonomy")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying them")
    args = parser.parse_args()

    print("=" * 60)
    print("Cerid AI — Taxonomy Migration (Phase 8C)")
    if args.dry_run:
        print("  MODE: DRY RUN (no changes will be made)")
    print("=" * 60)
    print()

    driver = get_driver()
    try:
        stats = migrate(driver, dry_run=args.dry_run)
    finally:
        driver.close()

    print()
    print("Migration complete!")
    print(f"  Domains:        {stats['domains_created']}")
    print(f"  Sub-categories: {stats['subcategories_created']}")
    print(f"  Artifacts:      {stats['artifacts_updated']} updated")
    print(f"  Relationships:  {stats['categorized_as_created']} CATEGORIZED_AS created")
    if args.dry_run:
        print()
        print("  This was a dry run. Run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
