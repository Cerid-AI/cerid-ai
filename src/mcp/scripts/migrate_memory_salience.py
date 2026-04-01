# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase 51 one-time migration: backfill memory salience fields.

Updates both Neo4j and ChromaDB for existing conversation-domain memories:
- memory_type: maps legacy ``fact`` → ``empirical``, ``action_item`` → ``project_context``
- stability_days: per-type default from config
- source_authority: defaults to 0.7 (llm_extracted)
- access_log: initialised to empty list in Neo4j

Usage (run inside Docker MCP container):
    python -m scripts.migrate_memory_salience [--dry-run]
"""

from __future__ import annotations

import argparse
import logging

import chromadb
from neo4j import GraphDatabase

import config
from db.neo4j.migrations import migrate_memory_salience
from errors import CeridError

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate-salience")


def migrate_chromadb(chroma_client: chromadb.ClientAPI, *, dry_run: bool = False) -> int:
    """Backfill salience metadata on ChromaDB conversation-domain chunks."""
    coll_name = config.collection_name("conversations")
    try:
        collection = chroma_client.get_collection(name=coll_name)
    except (CeridError, ValueError, OSError, RuntimeError):
        logger.warning("Collection %s not found — nothing to migrate", coll_name)
        return 0

    # Fetch all chunks (ChromaDB get() without ids returns everything)
    all_data = collection.get(include=["metadatas"])
    if not all_data["ids"]:
        logger.info("No chunks in %s", coll_name)
        return 0

    updated = 0
    for i, chunk_id in enumerate(all_data["ids"]):
        meta = all_data["metadatas"][i] if all_data["metadatas"] else {}

        # Skip already-migrated chunks (have source_authority)
        if "source_authority" in meta:
            continue

        raw_type = meta.get("memory_type", "empirical")
        new_type = config.MEMORY_TYPE_MIGRATION.get(raw_type, raw_type)
        if new_type not in config.MEMORY_TYPES:
            new_type = "empirical"

        stability = config.MEMORY_TYPE_STABILITY.get(new_type, 30.0)
        stability_str = "inf" if stability == float("inf") else str(stability)

        new_meta = {
            "memory_type": new_type,
            "stability_days": stability_str,
            "source_authority": str(config.DEFAULT_SOURCE_AUTHORITY),
        }

        if dry_run:
            logger.info("[DRY RUN] Would update chunk %s: %s → %s", chunk_id, raw_type, new_type)
        else:
            collection.update(ids=[chunk_id], metadatas=[new_meta])

        updated += 1

    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 51 memory salience migration")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    # --- Neo4j ---
    logger.info("=== Neo4j migration ===")
    neo4j_uri = config.NEO4J_URI
    neo4j_user = config.NEO4J_USER
    neo4j_password = config.NEO4J_PASSWORD

    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
    try:
        if args.dry_run:
            with driver.session() as session:
                result = session.run(
                    "MATCH (a:Artifact)-[:BELONGS_TO]->(:Domain {name: 'conversations'}) "
                    "WHERE a.source_authority IS NULL "
                    "RETURN count(a) AS cnt"
                )
                cnt = result.single()["cnt"]
            logger.info("[DRY RUN] Would migrate %d Neo4j artifacts", cnt)
        else:
            result = migrate_memory_salience(driver)
            logger.info("Neo4j: migrated %d artifacts", result["migrated"])
    finally:
        driver.close()

    # --- ChromaDB ---
    logger.info("=== ChromaDB migration ===")
    from deps import parse_chroma_url

    host, port = parse_chroma_url()
    chroma_client = chromadb.HttpClient(host=host, port=port)
    updated = migrate_chromadb(chroma_client, dry_run=args.dry_run)
    logger.info("ChromaDB: %s %d chunks", "would update" if args.dry_run else "updated", updated)

    logger.info("=== Migration complete ===")


if __name__ == "__main__":
    main()
