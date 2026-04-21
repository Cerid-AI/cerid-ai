# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Cerid AI - Auto-import on startup if local DB is empty and sync data exists.
Called from main.py lifespan after graph.init_schema().
"""
import logging
import os

from errors import SyncError

logger = logging.getLogger("ai-companion.sync")


def auto_import_if_empty():
    """Check if Neo4j is empty and sync dir has valid data. If so, auto-import."""
    import config

    sync_dir = getattr(config, "SYNC_DIR", os.path.expanduser("~/Dropbox/cerid-sync"))
    manifest_path = os.path.join(sync_dir, "manifest.json")

    if not os.path.exists(manifest_path):
        logger.info("No sync manifest found — skipping auto-import")
        return

    # Check if Neo4j has any artifacts
    from deps import get_neo4j
    driver = get_neo4j()
    if driver is None:
        logger.info("Lightweight mode — skipping sync auto-import (Neo4j unavailable)")
        return
    try:
        with driver.session() as session:
            result = session.run("MATCH (a:Artifact) RETURN count(a) AS cnt")
            count = result.single()["cnt"]
    except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning(f"Could not check Neo4j artifact count: {e}")
        return

    if count > 0:
        logger.info(f"Neo4j has {count} artifacts — skipping auto-import")
        return

    # Neo4j is empty and sync manifest exists — run import
    logger.info("Empty database detected with valid sync manifest — starting auto-import")
    try:
        import redis as redis_lib

        from sync import (
            import_bm25,
            import_chroma,
            import_neo4j,
            import_redis,
            read_manifest,
        )

        manifest = read_manifest(sync_dir)
        if not manifest:
            logger.warning("Invalid sync manifest — skipping auto-import")
            return

        chroma_url = config.CHROMA_URL
        redis_client = redis_lib.from_url(config.REDIS_URL)

        neo_result = import_neo4j(driver, sync_dir)
        logger.info(f"Neo4j import: {neo_result}")

        chroma_result = import_chroma(chroma_url, sync_dir)
        logger.info(f"ChromaDB import: {chroma_result}")

        bm25_result = import_bm25(sync_dir)
        logger.info(f"BM25 import: {bm25_result}")

        redis_result = import_redis(redis_client, sync_dir)
        logger.info(f"Redis import: {redis_result}")

        logger.info("Auto-import complete")
    except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning(f"Auto-import failed: {e}")
