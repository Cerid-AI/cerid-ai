# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sync export — Neo4j, ChromaDB, BM25, Redis to JSONL files."""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

import config

from sync._helpers import (
    ARTIFACTS_JSONL,
    AUDIT_LOG_JSONL,
    BM25_SUBDIR,
    CHROMA_BATCH_SIZE,
    CHROMA_SUBDIR,
    DOMAINS_JSONL,
    NEO4J_SUBDIR,
    REDIS_SUBDIR,
    RELATIONSHIPS_JSONL,
    _default_sync_dir,
    _ensure_dir,
    _write_jsonl,
)
from sync.manifest import write_manifest

logger = logging.getLogger("ai-companion.sync")


def export_neo4j(driver, sync_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Export all Artifact nodes, Domain nodes, and inter-artifact relationships
    from Neo4j to JSONL files under {sync_dir}/neo4j/.
    """
    sync_dir = sync_dir or _default_sync_dir()
    out_dir = _ensure_dir(os.path.join(sync_dir, NEO4J_SUBDIR))

    artifacts: List[Dict[str, Any]] = []
    domains: List[Dict[str, Any]] = []
    relationships: List[Dict[str, Any]] = []

    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (a:Artifact)
                RETURN
                    a.id            AS id,
                    a.filename      AS filename,
                    a.domain        AS domain,
                    a.keywords      AS keywords,
                    a.summary       AS summary,
                    a.chunk_count   AS chunk_count,
                    a.chunk_ids     AS chunk_ids,
                    a.content_hash  AS content_hash,
                    a.ingested_at   AS ingested_at,
                    a.modified_at   AS modified_at,
                    a.recategorized_at AS recategorized_at
                ORDER BY a.ingested_at ASC
                """
            )
            for record in result:
                artifacts.append(dict(record))

            result = session.run(
                "MATCH (d:Domain) RETURN d.name AS name ORDER BY d.name"
            )
            for record in result:
                domains.append(dict(record))

            rel_types = "|".join(config.GRAPH_RELATIONSHIP_TYPES)
            result = session.run(
                f"""
                MATCH (s:Artifact)-[r:{rel_types}]->(t:Artifact)
                RETURN
                    s.id            AS source_id,
                    t.id            AS target_id,
                    type(r)         AS rel_type,
                    r.reason        AS reason,
                    r.overlap_count AS overlap_count,
                    r.created_at    AS created_at
                ORDER BY r.created_at ASC
                """
            )
            for record in result:
                relationships.append(dict(record))

    except Exception as exc:
        logger.error("Neo4j export failed: %s", exc)
        return {"error": str(exc), "artifacts": 0, "domains": 0, "relationships": 0}

    a_count = _write_jsonl(str(out_dir / ARTIFACTS_JSONL), artifacts)
    d_count = _write_jsonl(str(out_dir / DOMAINS_JSONL), domains)
    r_count = _write_jsonl(str(out_dir / RELATIONSHIPS_JSONL), relationships)

    logger.info(
        "Neo4j export complete: %d artifacts, %d domains, %d relationships → %s",
        a_count, d_count, r_count, out_dir,
    )
    return {
        "artifacts": a_count,
        "domains": d_count,
        "relationships": r_count,
        "output_dir": str(out_dir),
    }


def export_chroma(chroma_url: Optional[str] = None, sync_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Export all ChromaDB collections (one per domain) to per-domain JSONL files
    under {sync_dir}/chroma/.
    """
    chroma_url = chroma_url or config.CHROMA_URL
    sync_dir = sync_dir or _default_sync_dir()
    out_dir = _ensure_dir(os.path.join(sync_dir, CHROMA_SUBDIR))

    domain_counts: Dict[str, int] = {}
    total_chunks = 0

    for domain in config.DOMAINS:
        coll_name = config.collection_name(domain)
        out_path = str(out_dir / f"{coll_name}.jsonl")
        chunk_count = 0

        try:
            coll_resp = httpx.get(
                f"{chroma_url}/api/v1/collections/{coll_name}",
                timeout=30.0,
            )
            if coll_resp.status_code in (400, 404):
                logger.warning("ChromaDB collection %s not found — skipping", coll_name)
                domain_counts[domain] = 0
                _write_jsonl(out_path, [])
                continue
            coll_resp.raise_for_status()
            coll_data = coll_resp.json()
            collection_id = coll_data.get("id", coll_name)

            with open(out_path, "w", encoding="utf-8") as fh:
                offset = 0
                while True:
                    resp = httpx.post(
                        f"{chroma_url}/api/v1/collections/{collection_id}/get",
                        json={
                            "include": ["documents", "metadatas", "embeddings"],
                            "limit": CHROMA_BATCH_SIZE,
                            "offset": offset,
                        },
                        timeout=120.0,
                    )
                    resp.raise_for_status()
                    batch = resp.json()

                    ids: List[str] = batch.get("ids", [])
                    if not ids:
                        break

                    documents: List[str] = batch.get("documents", [])
                    metadatas: List[Dict] = batch.get("metadatas", [])
                    embeddings: List[List[float]] = batch.get("embeddings", [])

                    for i, chunk_id in enumerate(ids):
                        row = {
                            "id": chunk_id,
                            "document": documents[i] if i < len(documents) else "",
                            "metadata": metadatas[i] if i < len(metadatas) else {},
                            "embedding": embeddings[i] if i < len(embeddings) else [],
                        }
                        fh.write(json.dumps(row) + "\n")
                        chunk_count += 1

                    offset += len(ids)
                    if len(ids) < CHROMA_BATCH_SIZE:
                        break

        except httpx.HTTPStatusError as exc:
            logger.error("ChromaDB HTTP error for %s: %s", coll_name, exc)
            domain_counts[domain] = chunk_count
            continue
        except Exception as exc:
            logger.error("ChromaDB export failed for %s: %s", coll_name, exc)
            domain_counts[domain] = chunk_count
            continue

        domain_counts[domain] = chunk_count
        total_chunks += chunk_count
        logger.info("ChromaDB exported %d chunks for domain '%s'", chunk_count, domain)

    logger.info("ChromaDB export complete: %d total chunks → %s", total_chunks, out_dir)
    return {
        "domains": domain_counts,
        "total_chunks": total_chunks,
        "output_dir": str(out_dir),
    }


def export_bm25(sync_dir: Optional[str] = None) -> Dict[str, Any]:
    """Copy BM25 JSONL corpus files from config.BM25_DATA_DIR to {sync_dir}/bm25/."""
    sync_dir = sync_dir or _default_sync_dir()
    out_dir = _ensure_dir(os.path.join(sync_dir, BM25_SUBDIR))
    src_dir = Path(config.BM25_DATA_DIR)

    copied = 0
    skipped = 0

    if not src_dir.exists():
        logger.warning("BM25 source directory does not exist: %s — skipping export", src_dir)
        return {"files_copied": 0, "files_skipped": 0, "output_dir": str(out_dir)}

    for src_file in src_dir.glob("*.jsonl"):
        dst_file = out_dir / src_file.name
        try:
            shutil.copy2(str(src_file), str(dst_file))
            copied += 1
            logger.debug("BM25 copied: %s → %s", src_file.name, dst_file)
        except OSError as exc:
            logger.warning("BM25 copy failed for %s: %s", src_file.name, exc)
            skipped += 1

    logger.info("BM25 export complete: %d files copied, %d skipped → %s", copied, skipped, out_dir)
    return {
        "files_copied": copied,
        "files_skipped": skipped,
        "output_dir": str(out_dir),
    }


def export_redis(redis_client, sync_dir: Optional[str] = None) -> Dict[str, Any]:
    """Dump the full Redis ingest:log list to {sync_dir}/redis/audit_log.jsonl."""
    sync_dir = sync_dir or _default_sync_dir()
    out_dir = _ensure_dir(os.path.join(sync_dir, REDIS_SUBDIR))
    out_path = str(out_dir / AUDIT_LOG_JSONL)

    entries_exported = 0

    try:
        raw_entries: List[str] = redis_client.lrange(config.REDIS_INGEST_LOG, 0, -1)
    except Exception as exc:
        logger.error("Redis LRANGE failed: %s", exc)
        return {"error": str(exc), "entries_exported": 0, "output_dir": str(out_dir)}

    # Redis list is newest-first (LPUSH); reverse to chronological order for export
    raw_entries = list(reversed(raw_entries))

    with open(out_path, "w", encoding="utf-8") as fh:
        for raw in raw_entries:
            try:
                parsed = json.loads(raw)
                fh.write(json.dumps(parsed, default=str) + "\n")
                entries_exported += 1
            except json.JSONDecodeError as exc:
                logger.warning("Skipping malformed Redis entry: %s", exc)

    logger.info("Redis export complete: %d entries → %s", entries_exported, out_path)
    return {
        "entries_exported": entries_exported,
        "output_dir": str(out_dir),
    }


def export_all(
    driver,
    chroma_url: Optional[str] = None,
    redis_client=None,
    sync_dir: Optional[str] = None,
    machine_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run all export steps in sequence and write a manifest."""
    chroma_url = chroma_url or config.CHROMA_URL
    sync_dir = sync_dir or _default_sync_dir()

    logger.info("Starting full export to %s", sync_dir)

    neo4j_result = export_neo4j(driver, sync_dir=sync_dir)
    chroma_result = export_chroma(chroma_url=chroma_url, sync_dir=sync_dir)
    bm25_result = export_bm25(sync_dir=sync_dir)

    redis_result: Dict[str, Any] = {"entries_exported": 0, "skipped": True}
    if redis_client is not None:
        redis_result = export_redis(redis_client, sync_dir=sync_dir)
    else:
        logger.warning("No Redis client provided — skipping Redis export")

    manifest = write_manifest(sync_dir=sync_dir, machine_id=machine_id)

    logger.info("Full export complete to %s", sync_dir)
    return {
        "neo4j": neo4j_result,
        "chroma": chroma_result,
        "bm25": bm25_result,
        "redis": redis_result,
        "manifest": manifest,
    }
