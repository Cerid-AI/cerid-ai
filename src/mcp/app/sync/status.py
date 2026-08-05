# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Sync status comparison — local DB counts vs sync manifest."""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

import httpx

import config
from app.sync._helpers import (
    ARTIFACTS_JSONL,
    AUDIT_LOG_JSONL,
    CHROMA_SUBDIR,
    DOMAINS_JSONL,
    ENTITIES_JSONL,
    MEMORIES_JSONL,
    NEO4J_SUBDIR,
    REDIS_SUBDIR,
    RELATIONSHIPS_JSONL,
    _default_sync_dir,
    _v2_collections_base,
)
from app.sync.manifest import read_manifest

logger = logging.getLogger("ai-companion.sync")


def compare_status(
    driver,
    chroma_url: str | None = None,
    redis_client=None,
    sync_dir: str | None = None,
) -> dict[str, Any]:
    """
    Compare local database counts against the counts recorded in the sync manifest.
    """
    chroma_url = chroma_url or config.CHROMA_URL
    sync_dir = sync_dir or _default_sync_dir()

    # --- Local counts ---
    local: dict[str, Any] = {
        "neo4j_artifacts": 0,
        "neo4j_domains": 0,
        "neo4j_relationships": 0,
        "neo4j_memories": 0,
        "neo4j_entities": 0,
        "chroma_chunks": {},
        "redis_entries": 0,
    }

    try:
        with driver.session() as session:
            local["neo4j_artifacts"] = session.run(
                "MATCH (a:Artifact) RETURN count(a) AS n"
            ).single()["n"]
            local["neo4j_domains"] = session.run(
                "MATCH (d:Domain) RETURN count(d) AS n"
            ).single()["n"]
            rel_types = "|".join(config.GRAPH_RELATIONSHIP_TYPES)
            local["neo4j_relationships"] = session.run(
                f"MATCH ()-[r:{rel_types}]->() RETURN count(r) AS n"
            ).single()["n"]
            local["neo4j_memories"] = session.run(
                "MATCH (m:Memory) RETURN count(m) AS n"
            ).single()["n"]
            local["neo4j_entities"] = session.run(
                "MATCH (e:Entity) RETURN count(e) AS n"
            ).single()["n"]
    except Exception as exc:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('app.sync.status', exc)
        logger.warning("Neo4j local count failed: %s", exc)

    for domain in config.DOMAINS:
        coll_name = config.collection_name(domain)
        try:
            coll_resp = httpx.get(
                f"{_v2_collections_base(chroma_url)}/{coll_name}", timeout=10.0
            )
            if coll_resp.status_code == HTTPStatus.OK:
                coll_id = coll_resp.json().get("id", coll_name)
                count_resp = httpx.get(
                    f"{_v2_collections_base(chroma_url)}/{coll_id}/count", timeout=10.0
                )
                if count_resp.status_code == HTTPStatus.OK:
                    local["chroma_chunks"][domain] = count_resp.json()
                else:
                    local["chroma_chunks"][domain] = 0
            else:
                local["chroma_chunks"][domain] = 0
        except Exception as exc:
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error('app.sync.status', exc)
            logger.warning("ChromaDB local count failed for %s: %s", domain, exc)
            local["chroma_chunks"][domain] = 0

    if redis_client is not None:
        try:
            local["redis_entries"] = redis_client.llen(config.REDIS_INGEST_LOG)
        except Exception as exc:
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error('app.sync.status', exc)
            logger.warning("Redis local count failed: %s", exc)

    # --- Sync counts (from manifest + JSONL line counts) ---
    sync: dict[str, Any] = {
        "neo4j_artifacts": 0,
        "neo4j_domains": 0,
        "neo4j_relationships": 0,
        "neo4j_memories": 0,
        "neo4j_entities": 0,
        "chroma_chunks": {d: 0 for d in config.DOMAINS},
        "redis_entries": 0,
    }
    manifest = None

    try:
        manifest = read_manifest(sync_dir)
        files = manifest.get("files", {})

        def _manifest_count(rel: str) -> int:
            return files.get(rel, {}).get("count", 0)

        sync["neo4j_artifacts"] = _manifest_count(f"{NEO4J_SUBDIR}/{ARTIFACTS_JSONL}")
        sync["neo4j_domains"] = _manifest_count(f"{NEO4J_SUBDIR}/{DOMAINS_JSONL}")
        sync["neo4j_relationships"] = _manifest_count(f"{NEO4J_SUBDIR}/{RELATIONSHIPS_JSONL}")
        sync["neo4j_memories"] = _manifest_count(f"{NEO4J_SUBDIR}/{MEMORIES_JSONL}")
        sync["neo4j_entities"] = _manifest_count(f"{NEO4J_SUBDIR}/{ENTITIES_JSONL}")
        sync["redis_entries"] = _manifest_count(f"{REDIS_SUBDIR}/{AUDIT_LOG_JSONL}")

        for domain in config.DOMAINS:
            rel = f"{CHROMA_SUBDIR}/domain_{domain}.jsonl"
            sync["chroma_chunks"][domain] = _manifest_count(rel)

    except FileNotFoundError:
        logger.warning("No manifest found at %s — sync counts will be 0", sync_dir)
    except ValueError as exc:
        logger.warning("Manifest parse error: %s", exc)

    # --- Diff ---
    diff: dict[str, Any] = {}
    for key in (
        "neo4j_artifacts", "neo4j_domains", "neo4j_relationships",
        "neo4j_memories", "neo4j_entities", "redis_entries",
    ):
        diff[key] = local[key] - sync[key]

    diff["chroma_chunks"] = {
        d: local["chroma_chunks"].get(d, 0) - sync["chroma_chunks"].get(d, 0)
        for d in config.DOMAINS
    }

    return {
        "sync_dir": str(sync_dir),
        "manifest": manifest,
        "local": local,
        "sync": sync,
        "diff": diff,
    }
