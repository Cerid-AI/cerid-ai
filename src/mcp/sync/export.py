# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sync export — Neo4j, ChromaDB, BM25, Redis to JSONL files."""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

import httpx

import config
from errors import SyncError
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


def export_neo4j(
    driver,
    sync_dir: str | None = None,
    since: str | None = None,
    domains: list[str] | None = None,
) -> dict[str, Any]:
    """
    Export Artifact nodes, Domain nodes, and inter-artifact relationships
    from Neo4j to JSONL files under {sync_dir}/neo4j/.

    If *since* is given (ISO-8601), only artifacts with updated_at > since
    are exported (incremental). If *domains* is given, only matching artifacts.
    """
    sync_dir = sync_dir or _default_sync_dir()
    out_dir = _ensure_dir(os.path.join(sync_dir, NEO4J_SUBDIR))

    artifacts: list[dict[str, Any]] = []
    artifact_ids: set[str] = set()
    domains_list: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []

    try:
        with driver.session() as session:
            # Build dynamic WHERE clause for incremental/selective export
            where_parts: list[str] = []
            params: dict[str, Any] = {}
            if since:
                where_parts.append(
                    "coalesce(a.updated_at, a.ingested_at) > $since"
                )
                params["since"] = since
            if domains:
                where_parts.append("a.domain IN $filter_domains")
                params["filter_domains"] = domains

            where_clause = ""
            if where_parts:
                where_clause = "WHERE " + " AND ".join(where_parts)

            result = session.run(
                f"""
                MATCH (a:Artifact)
                {where_clause}
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
                    a.recategorized_at AS recategorized_at,
                    a.updated_at    AS updated_at
                ORDER BY a.ingested_at ASC
                """,
                **params,
            )
            for record in result:
                row = dict(record)
                artifacts.append(row)
                artifact_ids.add(row["id"])

            result = session.run(
                "MATCH (d:Domain) RETURN d.name AS name ORDER BY d.name"
            )
            for record in result:
                domains_list.append(dict(record))

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

    except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.error("Neo4j export failed: %s", exc)
        return {"error": str(exc), "artifacts": 0, "domains": 0, "relationships": 0}

    a_count = _write_jsonl(str(out_dir / ARTIFACTS_JSONL), artifacts)
    d_count = _write_jsonl(str(out_dir / DOMAINS_JSONL), domains_list)
    r_count = _write_jsonl(str(out_dir / RELATIONSHIPS_JSONL), relationships)

    mode = "incremental" if since else "full"
    logger.info(
        "Neo4j export (%s): %d artifacts, %d domains, %d relationships → %s",
        mode, a_count, d_count, r_count, out_dir,
    )
    return {
        "artifacts": a_count,
        "domains": d_count,
        "relationships": r_count,
        "artifact_ids": artifact_ids,
        "is_incremental": since is not None,
        "output_dir": str(out_dir),
    }


def export_chroma(
    chroma_url: str | None = None,
    sync_dir: str | None = None,
    artifact_ids: set[str] | None = None,
    filter_domains: list[str] | None = None,
) -> dict[str, Any]:
    """
    Export ChromaDB collections to per-domain JSONL files under {sync_dir}/chroma/.

    If *artifact_ids* is given, only chunks belonging to those artifacts are exported
    (for incremental sync). If *filter_domains* is given, only those domains.
    """
    chroma_url = chroma_url or config.CHROMA_URL
    sync_dir = sync_dir or _default_sync_dir()
    out_dir = _ensure_dir(os.path.join(sync_dir, CHROMA_SUBDIR))

    domain_counts: dict[str, int] = {}
    total_chunks = 0
    target_domains = filter_domains or config.DOMAINS

    for domain in target_domains:
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

                    ids: list[str] = batch.get("ids", [])
                    if not ids:
                        break

                    documents: list[str] = batch.get("documents", [])
                    metadatas: list[dict] = batch.get("metadatas", [])
                    embeddings: list[list[float]] = batch.get("embeddings", [])

                    for i, chunk_id in enumerate(ids):
                        meta = metadatas[i] if i < len(metadatas) else {}
                        # Incremental filter: skip chunks not in the delta set
                        if artifact_ids is not None:
                            chunk_artifact = meta.get("artifact_id", "")
                            if chunk_artifact not in artifact_ids:
                                continue
                        row = {
                            "id": chunk_id,
                            "document": documents[i] if i < len(documents) else "",
                            "metadata": meta,
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
        except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
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


def export_bm25(sync_dir: str | None = None) -> dict[str, Any]:
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


def export_redis(redis_client, sync_dir: str | None = None) -> dict[str, Any]:
    """Dump the full Redis ingest:log list to {sync_dir}/redis/audit_log.jsonl."""
    sync_dir = sync_dir or _default_sync_dir()
    out_dir = _ensure_dir(os.path.join(sync_dir, REDIS_SUBDIR))
    out_path = str(out_dir / AUDIT_LOG_JSONL)

    entries_exported = 0

    try:
        raw_entries: list[str] = redis_client.lrange(config.REDIS_INGEST_LOG, 0, -1)
    except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
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
    chroma_url: str | None = None,
    redis_client=None,
    sync_dir: str | None = None,
    machine_id: str | None = None,
    since: str | None = None,
    domains: list[str] | None = None,
) -> dict[str, Any]:
    """Run all export steps in sequence and write a manifest.

    If *since* is given (ISO-8601), performs an incremental export of only
    artifacts modified after that timestamp. If *domains* is given, limits
    to those domains.
    """
    from utils.time import utcnow_iso as _utcnow

    chroma_url = chroma_url or config.CHROMA_URL
    sync_dir = sync_dir or _default_sync_dir()
    mode = "incremental" if since else "full"
    export_start = _utcnow()  # capture before queries to avoid TOCTOU gaps

    logger.info("Starting %s export to %s", mode, sync_dir)

    neo4j_result = export_neo4j(driver, sync_dir=sync_dir, since=since, domains=domains)
    # Pass artifact_ids from neo4j delta to ChromaDB for filtered export
    delta_ids = neo4j_result.get("artifact_ids")
    chroma_result = export_chroma(
        chroma_url=chroma_url,
        sync_dir=sync_dir,
        artifact_ids=delta_ids if since else None,
        filter_domains=domains,
    )
    bm25_result = export_bm25(sync_dir=sync_dir)

    redis_result: dict[str, Any] = {"entries_exported": 0, "skipped": True}
    if redis_client is not None:
        redis_result = export_redis(redis_client, sync_dir=sync_dir)
    else:
        logger.warning("No Redis client provided — skipping Redis export")

    # Export tombstones (merge local → sync dir, purge expired)
    tombstone_result: dict[str, Any] = {"tombstones_exported": 0}
    try:
        from sync.tombstones import export_tombstones
        tombstone_result = export_tombstones(sync_dir=sync_dir)
    except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.warning("Tombstone export failed (non-blocking): %s", exc)

    manifest = write_manifest(
        sync_dir=sync_dir,
        machine_id=machine_id,
        is_incremental=since is not None,
        last_exported_at=export_start,
    )

    logger.info("%s export complete to %s", mode.capitalize(), sync_dir)
    return {
        "neo4j": neo4j_result,
        "chroma": chroma_result,
        "bm25": bm25_result,
        "redis": redis_result,
        "tombstones": tombstone_result,
        "manifest": manifest,
    }
