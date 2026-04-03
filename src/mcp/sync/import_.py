# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sync import — Neo4j, ChromaDB, BM25, Redis from JSONL files."""

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
    _count_jsonl_lines,
    _default_sync_dir,
    _ensure_dir,
    _iter_jsonl,
)
from sync.conflicts import (
    ConflictStrategy,
    detect_conflicts,
    resolve_conflicts,
    write_conflict_log,
)
from utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.sync")


def import_neo4j(
    driver,
    sync_dir: str | None = None,
    force: bool = False,
    conflict_strategy: str = "remote_wins",
    last_sync_at: str | None = None,
) -> dict[str, Any]:
    """
    Merge Neo4j data from sync_dir into the local graph.

    Domain nodes:       MERGE by name (always safe).
    Artifact nodes:     MERGE by id. If the remote ingested_at is newer (or force=True),
                        all properties are updated. Relationships follow the artifact.
    Relationships:      MERGE by (source_id, target_id, rel_type). Properties set on CREATE only.

    *conflict_strategy*: how to handle artifacts modified on both machines.
    *last_sync_at*: ISO timestamp from manifest for conflict window detection.
    """
    sync_dir = sync_dir or _default_sync_dir()
    neo4j_dir = Path(sync_dir) / NEO4J_SUBDIR

    domains_merged = 0
    artifacts_created = 0
    artifacts_updated = 0
    artifacts_skipped = 0
    artifacts_conflict = 0
    relationships_merged = 0

    # --- Conflict Detection ---
    skip_ids: set[str] = set()  # artifact IDs to skip due to conflict resolution
    conflict_records: list[Any] = []
    try:
        strategy = ConflictStrategy(conflict_strategy)
    except ValueError:
        strategy = ConflictStrategy.REMOTE_WINS

    if last_sync_at and strategy != ConflictStrategy.REMOTE_WINS:
        artifacts_path_pre = str(neo4j_dir / ARTIFACTS_JSONL)
        remote_rows = list(_iter_jsonl(artifacts_path_pre))
        conflict_records = detect_conflicts(driver, remote_rows, last_sync_at)
        if conflict_records:
            resolutions = resolve_conflicts(conflict_records, strategy)
            for aid, res in resolutions.items():
                if res in (
                    ConflictStrategy.LOCAL_WINS,
                    ConflictStrategy.KEEP_BOTH,
                    ConflictStrategy.MANUAL_REVIEW,
                ):
                    skip_ids.add(aid)
            if strategy == ConflictStrategy.KEEP_BOTH:
                logger.warning(
                    "KEEP_BOTH strategy: %d conflicts deferred to manual review "
                    "(automatic ID-cloning not yet implemented)",
                    len(conflict_records),
                )
            # Write conflict log for manual_review / keep_both entries
            review_conflicts = [
                c for c in conflict_records
                if c.resolution in ("manual_review", "keep_both")
            ]
            if review_conflicts:
                write_conflict_log(review_conflicts, sync_dir=sync_dir)

    # --- Domains ---
    domains_path = str(neo4j_dir / DOMAINS_JSONL)
    with driver.session() as session:
        for row in _iter_jsonl(domains_path):
            name = row.get("name")
            if not name:
                continue
            try:
                session.run("MERGE (:Domain {name: $name})", name=name)
                domains_merged += 1
            except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
                logger.warning("Failed to merge Domain '%s': %s", name, exc)

    # --- Artifacts ---
    artifacts_path = str(neo4j_dir / ARTIFACTS_JSONL)
    with driver.session() as session:
        for row in _iter_jsonl(artifacts_path):
            artifact_id = row.get("id")
            if not artifact_id:
                continue

            # Skip artifacts flagged by conflict resolution (local_wins / manual_review)
            if artifact_id in skip_ids:
                artifacts_conflict += 1
                continue

            try:
                existing = session.run(
                    "MATCH (a:Artifact {id: $id}) "
                    "RETURN a.updated_at AS updated_at, a.ingested_at AS ingested_at",
                    id=artifact_id,
                ).single()

                remote_updated = row.get("updated_at") or row.get("ingested_at") or ""
                local_updated = (
                    (existing["updated_at"] or existing["ingested_at"])
                    if existing else None
                )

                should_update = (
                    force
                    or local_updated is None
                    or remote_updated > local_updated
                )

                remote_ingested_at = row.get("ingested_at") or ""

                if local_updated is None:
                    session.run(
                        """
                        MERGE (d:Domain {name: $domain})
                        CREATE (a:Artifact {
                            id:               $id,
                            filename:         $filename,
                            domain:           $domain,
                            keywords:         $keywords,
                            summary:          $summary,
                            chunk_count:      $chunk_count,
                            chunk_ids:        $chunk_ids,
                            content_hash:     $content_hash,
                            ingested_at:      $ingested_at,
                            modified_at:      $modified_at,
                            recategorized_at: $recategorized_at,
                            updated_at:       $updated_at
                        })
                        MERGE (a)-[:BELONGS_TO]->(d)
                        """,
                        id=artifact_id,
                        filename=row.get("filename", ""),
                        domain=row.get("domain", config.DEFAULT_DOMAIN),
                        keywords=row.get("keywords", "[]"),
                        summary=row.get("summary", ""),
                        chunk_count=row.get("chunk_count", 0),
                        chunk_ids=row.get("chunk_ids", "[]"),
                        content_hash=row.get("content_hash", ""),
                        ingested_at=remote_ingested_at,
                        modified_at=row.get("modified_at"),
                        recategorized_at=row.get("recategorized_at"),
                        updated_at=row.get("updated_at") or remote_ingested_at,
                    )
                    artifacts_created += 1

                elif should_update:
                    session.run(
                        """
                        MATCH (a:Artifact {id: $id})
                        SET a.filename         = $filename,
                            a.domain           = $domain,
                            a.keywords         = $keywords,
                            a.summary          = $summary,
                            a.chunk_count      = $chunk_count,
                            a.chunk_ids        = $chunk_ids,
                            a.content_hash     = $content_hash,
                            a.ingested_at      = $ingested_at,
                            a.modified_at      = $modified_at,
                            a.recategorized_at = $recategorized_at,
                            a.updated_at       = $updated_at
                        WITH a
                        MATCH (a)-[r:BELONGS_TO]->(:Domain)
                        DELETE r
                        WITH a
                        MERGE (d:Domain {name: $domain})
                        MERGE (a)-[:BELONGS_TO]->(d)
                        """,
                        id=artifact_id,
                        filename=row.get("filename", ""),
                        domain=row.get("domain", config.DEFAULT_DOMAIN),
                        keywords=row.get("keywords", "[]"),
                        summary=row.get("summary", ""),
                        chunk_count=row.get("chunk_count", 0),
                        chunk_ids=row.get("chunk_ids", "[]"),
                        content_hash=row.get("content_hash", ""),
                        ingested_at=remote_ingested_at,
                        modified_at=row.get("modified_at"),
                        recategorized_at=row.get("recategorized_at"),
                        updated_at=row.get("updated_at") or remote_ingested_at,
                    )
                    artifacts_updated += 1

                else:
                    artifacts_skipped += 1

            except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
                logger.warning("Failed to import artifact %s: %s", artifact_id[:8], exc)

    # --- Relationships ---
    relationships_path = str(neo4j_dir / RELATIONSHIPS_JSONL)
    with driver.session() as session:
        for row in _iter_jsonl(relationships_path):
            source_id = row.get("source_id")
            target_id = row.get("target_id")
            rel_type = row.get("rel_type")

            if not (source_id and target_id and rel_type):
                continue
            if rel_type not in config.GRAPH_RELATIONSHIP_TYPES:
                logger.warning("Skipping unknown relationship type: %s", rel_type)
                continue

            try:
                props = {
                    "reason": row.get("reason"),
                    "overlap_count": row.get("overlap_count"),
                    "created_at": row.get("created_at") or utcnow_iso(),
                }
                props = {k: v for k, v in props.items() if v is not None}

                cypher = (
                    f"MATCH (s:Artifact {{id: $source_id}}), (t:Artifact {{id: $target_id}}) "
                    f"MERGE (s)-[r:{rel_type}]->(t) "
                    f"ON CREATE SET r += $props "
                    f"RETURN r IS NOT NULL AS ok"
                )
                session.run(cypher, source_id=source_id, target_id=target_id, props=props)
                relationships_merged += 1
            except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
                logger.warning(
                    "Failed to merge relationship %s→%s (%s): %s",
                    source_id[:8], target_id[:8], rel_type, exc,
                )

    logger.info(
        "Neo4j import complete: %d domains, %d created, %d updated, "
        "%d skipped, %d conflicts, %d relationships",
        domains_merged, artifacts_created, artifacts_updated,
        artifacts_skipped, artifacts_conflict, relationships_merged,
    )
    return {
        "domains_merged": domains_merged,
        "artifacts_created": artifacts_created,
        "artifacts_updated": artifacts_updated,
        "artifacts_skipped": artifacts_skipped,
        "artifacts_conflict": artifacts_conflict,
        "conflicts": [
            {"artifact_id": c.artifact_id, "resolution": c.resolution}
            for c in conflict_records
        ] if conflict_records else [],
        "relationships_merged": relationships_merged,
    }


def import_chroma(
    chroma_url: str | None = None,
    sync_dir: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """
    Merge ChromaDB chunks from sync JSONL files into the local ChromaDB instance.
    """
    chroma_url = chroma_url or config.CHROMA_URL
    sync_dir = sync_dir or _default_sync_dir()
    chroma_dir = Path(sync_dir) / CHROMA_SUBDIR

    domain_stats: dict[str, dict[str, int | str]] = {}
    total_added = 0
    total_skipped = 0

    for domain in config.DOMAINS:
        coll_name = config.collection_name(domain)
        src_path = str(chroma_dir / f"{coll_name}.jsonl")

        if not os.path.exists(src_path):
            logger.warning("No ChromaDB export file found for domain '%s': %s", domain, src_path)
            domain_stats[domain] = {"added": 0, "skipped": 0}
            continue

        added = 0
        skipped = 0

        try:
            _chroma_ensure_collection(chroma_url, coll_name)

            collection_id = _chroma_get_collection_id(chroma_url, coll_name)
            if not collection_id:
                logger.error("Cannot resolve collection ID for %s — skipping", coll_name)
                domain_stats[domain] = {"added": 0, "skipped": 0}
                continue

            existing_ids: set = set()
            if not force:
                existing_ids = _chroma_get_all_ids(chroma_url, collection_id)

            batch_ids: list[str] = []
            batch_docs: list[str] = []
            batch_metas: list[dict] = []
            batch_embs: list[list[float]] = []

            def _flush_batch() -> int:
                nonlocal added
                if not batch_ids:
                    return 0
                try:
                    resp = httpx.post(
                        f"{chroma_url}/api/v1/collections/{collection_id}/add",
                        json={
                            "ids": batch_ids,
                            "documents": batch_docs,
                            "metadatas": batch_metas,
                            "embeddings": batch_embs,
                        },
                        timeout=120.0,
                    )
                    resp.raise_for_status()
                    n = len(batch_ids)
                    added += n
                    return n
                except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
                    logger.error("ChromaDB batch add failed for %s: %s", coll_name, exc)
                    return 0
                finally:
                    batch_ids.clear()
                    batch_docs.clear()
                    batch_metas.clear()
                    batch_embs.clear()

            for row in _iter_jsonl(src_path):
                chunk_id = row.get("id")
                if not chunk_id:
                    continue

                if chunk_id in existing_ids:
                    skipped += 1
                    continue

                embedding = row.get("embedding")
                if not embedding:
                    logger.debug("Skipping chunk %s: no embedding in export", chunk_id)
                    skipped += 1
                    continue

                batch_ids.append(chunk_id)
                batch_docs.append(row.get("document", ""))
                batch_metas.append(row.get("metadata") or {})
                batch_embs.append(embedding)

                if len(batch_ids) >= CHROMA_BATCH_SIZE:
                    _flush_batch()

            _flush_batch()

        except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            logger.error("ChromaDB import failed for domain '%s': %s", domain, exc)
            domain_stats[domain] = {"added": added, "skipped": skipped, "error": str(exc)}
            continue

        domain_stats[domain] = {"added": added, "skipped": skipped}
        total_added += added
        total_skipped += skipped
        logger.info(
            "ChromaDB import domain '%s': %d added, %d skipped", domain, added, skipped
        )

    logger.info(
        "ChromaDB import complete: %d total added, %d total skipped",
        total_added, total_skipped,
    )
    return {
        "domains": domain_stats,
        "total_added": total_added,
        "total_skipped": total_skipped,
    }


def _chroma_ensure_collection(chroma_url: str, collection_name: str) -> None:
    """Create a ChromaDB collection if it does not already exist."""
    try:
        resp = httpx.get(f"{chroma_url}/api/v1/collections/{collection_name}", timeout=15.0)
        if resp.status_code == 200:
            return
        # ChromaDB 0.5.x returns 400 (not 404) for non-existent collections
        if resp.status_code in (400, 404):
            httpx.post(
                f"{chroma_url}/api/v1/collections",
                json={"name": collection_name},
                timeout=15.0,
            ).raise_for_status()
            logger.info("Created ChromaDB collection: %s", collection_name)
    except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.warning("Could not ensure collection %s: %s", collection_name, exc)


def _chroma_get_collection_id(chroma_url: str, collection_name: str) -> str | None:
    """Return the UUID for a named ChromaDB collection, or None on failure."""
    try:
        resp = httpx.get(
            f"{chroma_url}/api/v1/collections/{collection_name}",
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json().get("id")
    except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.warning("Cannot get ID for collection %s: %s", collection_name, exc)
        return None


def _chroma_get_all_ids(chroma_url: str, collection_id: str) -> set:
    """Retrieve all chunk IDs from a ChromaDB collection for deduplication."""
    ids: set = set()
    offset = 0
    while True:
        try:
            resp = httpx.post(
                f"{chroma_url}/api/v1/collections/{collection_id}/get",
                json={"include": [], "limit": CHROMA_BATCH_SIZE, "offset": offset},
                timeout=60.0,
            )
            resp.raise_for_status()
            batch_ids: list[str] = resp.json().get("ids", [])
            if not batch_ids:
                break
            ids.update(batch_ids)
            offset += len(batch_ids)
            if len(batch_ids) < CHROMA_BATCH_SIZE:
                break
        except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            logger.warning("Error fetching existing IDs at offset %d: %s", offset, exc)
            break
    return ids


def import_bm25(sync_dir: str | None = None) -> dict[str, Any]:
    """Merge BM25 corpus files from {sync_dir}/bm25/ into config.BM25_DATA_DIR."""
    sync_dir = sync_dir or _default_sync_dir()
    src_dir = Path(sync_dir) / BM25_SUBDIR
    dst_dir = Path(config.BM25_DATA_DIR)

    files_processed = 0
    chunks_added = 0
    chunks_skipped = 0

    if not src_dir.exists():
        logger.warning("BM25 sync source directory not found: %s — skipping import", src_dir)
        return {"files_processed": 0, "chunks_added": 0, "chunks_skipped": 0}

    _ensure_dir(str(dst_dir))

    for src_file in sorted(src_dir.glob("*.jsonl")):
        dst_file = dst_dir / src_file.name

        if not dst_file.exists():
            try:
                shutil.copy2(str(src_file), str(dst_file))
                line_count = _count_jsonl_lines(str(src_file))
                chunks_added += line_count
                files_processed += 1
                logger.debug("BM25 copied new corpus file: %s (%d chunks)", src_file.name, line_count)
            except OSError as exc:
                logger.warning("BM25 copy failed for %s: %s", src_file.name, exc)
            continue

        try:
            existing_ids: set = set()
            for row in _iter_jsonl(str(dst_file)):
                cid = row.get("chunk_id") or row.get("id")
                if cid:
                    existing_ids.add(cid)

            new_rows: list[dict[str, Any]] = []
            for row in _iter_jsonl(str(src_file)):
                cid = row.get("chunk_id") or row.get("id")
                if cid and cid not in existing_ids:
                    new_rows.append(row)
                    chunks_added += 1
                else:
                    chunks_skipped += 1

            if new_rows:
                with open(str(dst_file), "a", encoding="utf-8") as fh:
                    for row in new_rows:
                        fh.write(json.dumps(row, default=str) + "\n")
                logger.debug(
                    "BM25 merged %s: %d new, %d skipped", src_file.name, len(new_rows), chunks_skipped
                )

            files_processed += 1

        except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            logger.warning("BM25 merge failed for %s: %s", src_file.name, exc)

    logger.info(
        "BM25 import complete: %d files, %d chunks added, %d skipped",
        files_processed, chunks_added, chunks_skipped,
    )
    return {
        "files_processed": files_processed,
        "chunks_added": chunks_added,
        "chunks_skipped": chunks_skipped,
    }


def import_redis(
    redis_client,
    sync_dir: str | None = None,
) -> dict[str, Any]:
    """Append audit log entries from {sync_dir}/redis/audit_log.jsonl into Redis."""
    sync_dir = sync_dir or _default_sync_dir()
    src_path = str(Path(sync_dir) / REDIS_SUBDIR / AUDIT_LOG_JSONL)

    entries_added = 0
    entries_skipped = 0

    if not os.path.exists(src_path):
        logger.warning("Redis audit log export not found: %s — skipping import", src_path)
        return {"entries_added": 0, "entries_skipped": 0}

    existing_keys: set = set()
    try:
        raw_existing = redis_client.lrange(config.REDIS_INGEST_LOG, 0, -1)
        for raw in raw_existing:
            try:
                entry = json.loads(raw)
                key = (entry.get("artifact_id", ""), entry.get("timestamp", ""))
                existing_keys.add(key)
            except json.JSONDecodeError as e:
                logger.debug("Skipping malformed Redis log entry during dedup: %s", e)
    except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.error("Cannot read existing Redis log for dedup: %s", exc)
        return {"error": str(exc), "entries_added": 0, "entries_skipped": 0}

    new_entries: list[str] = []
    for row in _iter_jsonl(src_path):
        key = (row.get("artifact_id", ""), row.get("timestamp", ""))
        if key in existing_keys:
            entries_skipped += 1
            continue
        new_entries.append(json.dumps(row, default=str))
        entries_added += 1

    if new_entries:
        try:
            for entry_str in reversed(new_entries):
                redis_client.lpush(config.REDIS_INGEST_LOG, entry_str)
            redis_client.ltrim(config.REDIS_INGEST_LOG, 0, config.REDIS_LOG_MAX - 1)
        except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            logger.error("Redis LPUSH failed during import: %s", exc)
            return {"error": str(exc), "entries_added": entries_added, "entries_skipped": entries_skipped}

    logger.info(
        "Redis import complete: %d entries added, %d skipped", entries_added, entries_skipped
    )
    return {"entries_added": entries_added, "entries_skipped": entries_skipped}


def import_all(
    driver,
    chroma_url: str | None = None,
    redis_client=None,
    sync_dir: str | None = None,
    force: bool = False,
    conflict_strategy: str = "remote_wins",
) -> dict[str, Any]:
    """Run all import steps in sequence."""
    chroma_url = chroma_url or config.CHROMA_URL
    sync_dir = sync_dir or _default_sync_dir()

    logger.info("Starting full import from %s (force=%s)", sync_dir, force)

    # Read manifest for last_sync_at (used in conflict detection)
    last_sync_at: str | None = None
    try:
        from sync.manifest import read_manifest
        manifest = read_manifest(sync_dir)
        last_sync_at = manifest.get("last_exported_at")
    except (FileNotFoundError, ValueError) as e:
        logger.debug("No existing manifest for import (first sync or missing): %s", e)

    # Apply tombstones first (delete remote-deleted artifacts before importing new data)
    tombstone_result: dict[str, Any] = {"deleted": 0}
    try:
        from sync.tombstones import apply_tombstones
        tombstone_result = apply_tombstones(driver, chroma_url, sync_dir=sync_dir)
    except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.warning("Tombstone application failed (non-blocking): %s", exc)

    neo4j_result = import_neo4j(
        driver, sync_dir=sync_dir, force=force,
        conflict_strategy=conflict_strategy, last_sync_at=last_sync_at,
    )
    chroma_result = import_chroma(chroma_url=chroma_url, sync_dir=sync_dir, force=force)
    bm25_result = import_bm25(sync_dir=sync_dir)

    redis_result: dict[str, Any] = {"entries_added": 0, "skipped": True}
    if redis_client is not None:
        redis_result = import_redis(redis_client, sync_dir=sync_dir)
    else:
        logger.warning("No Redis client provided — skipping Redis import")

    # Post-import consistency check
    consistency_warnings: list[str] = []
    try:
        neo4j_created = neo4j_result.get("artifacts_created", 0)
        neo4j_updated = neo4j_result.get("artifacts_updated", 0)
        chroma_imported = chroma_result.get("total_added", 0)

        if (neo4j_created + neo4j_updated) > 0 and chroma_imported == 0:
            consistency_warnings.append(
                "Neo4j artifacts imported but no ChromaDB collections were imported. "
                "Data may be out of sync — re-run import to complete."
            )

        chroma_domains = chroma_result.get("domains", {})
        failed_domains = [d for d, stats in chroma_domains.items() if isinstance(stats, dict) and "error" in stats]
        if failed_domains:
            consistency_warnings.append(
                f"ChromaDB import failed for domains: {', '.join(failed_domains)}. "
                "Some domains may be missing chunks."
            )

        for warning in consistency_warnings:
            logger.warning("Consistency check: %s", warning)
    except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.warning("Post-import consistency check failed: %s", exc)

    # Rebuild BM25 in-memory indexes after importing new corpus files
    bm25_chunks_added = bm25_result.get("chunks_added", 0) if isinstance(bm25_result, dict) else 0
    if bm25_chunks_added > 0:
        try:
            from utils.bm25 import rebuild_all as bm25_rebuild_all
            rebuilt = bm25_rebuild_all()
            logger.info("BM25 indexes rebuilt for %d domains after import", rebuilt)
        except (SyncError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            logger.warning("BM25 index rebuild failed after import: %s", exc)

    logger.info("Full import complete from %s", sync_dir)
    return {
        "neo4j": neo4j_result,
        "chroma": chroma_result,
        "bm25": bm25_result,
        "redis": redis_result,
        "tombstones": tombstone_result,
        "consistency_warnings": consistency_warnings,
    }
