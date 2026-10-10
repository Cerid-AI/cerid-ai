# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Sync import — Neo4j, ChromaDB, BM25, Redis from JSONL files."""

from __future__ import annotations

import json
import logging
import os
from http import HTTPStatus
from pathlib import Path
from typing import Any

import httpx

import config
from app.services.content_lifecycle import CONVERSATIONS_DOMAIN, TRANSCRIPT_FILENAME_PREFIX
from app.sync._helpers import (
    ARTIFACTS_JSONL,
    AUDIT_LOG_JSONL,
    BM25_SUBDIR,
    CHROMA_BATCH_SIZE,
    CHROMA_SUBDIR,
    DOMAINS_JSONL,
    ENTITIES_JSONL,
    ENTITY_EDGES_JSONL,
    FACT_PROPS,
    FACTS_JSONL,
    IDENTITY_PROPS,
    LINEAGE_PROPS,
    MEMORIES_JSONL,
    MEMORY_EDGES_JSONL,
    NEO4J_SUBDIR,
    REDIS_SUBDIR,
    RELATIONSHIPS_JSONL,
    _default_sync_dir,
    _ensure_dir,
    _iter_jsonl,
    _v2_collections_base,
    without_wiki_page,
)
from app.sync.conflicts import (
    ConflictStrategy,
    detect_conflicts,
    resolve_conflicts,
    write_conflict_log,
)
from app.sync.user_state import read_conversations, write_conversation
from core.forget import registry as forget_registry
from core.lineage.current import VERSION_CLOSED
from core.retrieval.chunk_ids import chunk_artifact_id
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.sync")


def _is_forgotten_artifact(artifact_id: str) -> bool:
    """Forgotten per the registry. Content added again after a forget records
    ``readded`` against that forget, which makes it importable everywhere; the
    decision never compares timestamps from two machines."""
    return forget_registry.is_forgotten("artifact", artifact_id)


def _is_forgotten_transcript(meta: dict[str, Any]) -> bool:
    """A ``chat_*`` transcript row of a trashed or purged conversation.

    A trash records only the conversation, so its transcript artifacts are not
    forgotten by id; the conversation id on the row is what ties them to it.
    """
    cid = str(meta.get("conversation_id") or "")
    return (
        bool(cid)
        and str(meta.get("filename") or "").startswith(TRANSCRIPT_FILENAME_PREFIX)
        and forget_registry.is_forgotten("conversation", cid)
    )


_VERIFIED_MEMORY_ROW_PREFIX = "verified_memory_"


def _is_forgotten_verified_memory(row_id: str) -> bool:
    """A verified memory's recall document (``verified_memory_{id}``) whose
    memory is trashed or purged. Its ``artifact_id`` is the memory id, which
    the registry records under kind ``memory``, not ``artifact``."""
    return row_id.startswith(_VERIFIED_MEMORY_ROW_PREFIX) and forget_registry.is_forgotten(
        "memory", row_id[len(_VERIFIED_MEMORY_ROW_PREFIX):],
    )


def _is_forgotten_chunk(row_id: str, meta: dict[str, Any]) -> bool:
    """A forgotten passage, or a child of one (the parent is what was forgotten)."""
    parent = str(meta.get("parent_chunk_id") or "")
    return forget_registry.is_forgotten("chunk", row_id) or (
        bool(parent) and forget_registry.is_forgotten("chunk", parent)
    )


def _is_forgotten_lexical_row(row_id: str) -> bool:
    """A keyword-index row of a forgotten passage or a forgotten artifact."""
    from core.retrieval.chunk_ids import chunk_artifact_id
    aid = chunk_artifact_id(row_id)
    return forget_registry.is_forgotten("chunk", row_id) or bool(aid and _is_forgotten_artifact(aid))


def _forgotten_transcript_artifact_ids(sync_dir: str) -> set[str]:
    """Transcript artifact ids of forgotten conversations, read from the Chroma
    export: the conversation id is stamped on the rows, never on the node."""
    path = Path(sync_dir) / CHROMA_SUBDIR / f"{config.collection_name(CONVERSATIONS_DOMAIN)}.jsonl"
    found: set[str] = set()
    for row in _iter_jsonl(str(path)):
        meta = row.get("metadata") or {}
        if meta.get("artifact_id") and _is_forgotten_transcript(meta):
            found.add(str(meta["artifact_id"]))
    return found


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
    artifacts_skipped_forgotten = 0
    relationships_merged = 0
    applied_ids: list[str] = []

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
            except Exception as exc:
                from core.utils.swallowed import log_swallowed_error
                log_swallowed_error('app.sync.import_', exc)
                logger.warning("Failed to merge Domain '%s': %s", name, exc)

    # --- Artifacts ---
    artifacts_path = str(neo4j_dir / ARTIFACTS_JSONL)
    forgotten_transcripts = _forgotten_transcript_artifact_ids(sync_dir)
    with driver.session() as session:
        for row in _iter_jsonl(artifacts_path):
            artifact_id = row.get("id")
            if not artifact_id:
                continue

            # Skip artifacts flagged by conflict resolution (local_wins / manual_review)
            if artifact_id in skip_ids:
                artifacts_conflict += 1
                continue

            if artifact_id in forgotten_transcripts or _is_forgotten_artifact(str(artifact_id)):
                artifacts_skipped_forgotten += 1
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
                    _apply_lineage(session, artifact_id, row)
                    applied_ids.append(str(artifact_id))
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
                    _apply_lineage(session, artifact_id, row)
                    applied_ids.append(str(artifact_id))
                    artifacts_updated += 1

                else:
                    artifacts_skipped += 1

            except Exception as exc:
                from core.utils.swallowed import log_swallowed_error
                log_swallowed_error('app.sync.import_', exc)
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
            except Exception as exc:
                from core.utils.swallowed import log_swallowed_error
                log_swallowed_error('app.sync.import_', exc)
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
        "artifacts_skipped_forgotten": artifacts_skipped_forgotten,
        "applied_ids": applied_ids,
        "conflicts": [
            {"artifact_id": c.artifact_id, "resolution": c.resolution}
            for c in conflict_records
        ] if conflict_records else [],
        "relationships_merged": relationships_merged,
    }


def _apply_lineage(session: Any, artifact_id: str, row: dict[str, Any]) -> None:
    """Carry a version's lineage over with the artifact (spec §7), absent
    values included: a version reopened there has no ``valid_to``, and loses
    it here. A node with no lineage there (a machine that predates lineages,
    or a document never re-ingested) leaves this one's alone."""
    props = {p: row.get(p) for p in LINEAGE_PROPS} if row.get("lineage_id") else {}
    props.update({p: row[p] for p in IDENTITY_PROPS if row.get(p) is not None})
    if props:
        session.run("MATCH (a:Artifact {id: $id}) SET a += $props", id=artifact_id, props=props)


def _with_lineage_cleared(props: dict[str, Any]) -> dict[str, Any]:
    """A memory exported with its lineage: lineage fields it lacks are absent
    there, so they are cleared here (``SET +=`` with null removes them)."""
    if not props.get("lineage_id"):
        return props
    return {**dict.fromkeys(LINEAGE_PROPS), **props}


#: What a version change moves on a passage this machine already holds; its
#: text, owner and tenant never change (chunk ids are content-addressed).
_ROW_VERSION_KEYS = (*LINEAGE_PROPS, VERSION_CLOSED)


def import_chroma(
    chroma_url: str | None = None,
    sync_dir: str | None = None,
    force: bool = False,
    update_artifacts: set[str] | None = None,
) -> dict[str, Any]:
    """
    Merge ChromaDB chunks from sync JSONL files into the local ChromaDB instance.

    A row this machine already holds is skipped, except when its artifact was
    just taken from the other machine (``update_artifacts``): then its version
    fields are taken, so a version the other machine closed or reopened is
    closed or reopened here too. Only the version fields move, and only on a
    row whose id names that artifact: text, owner and tenant never change.
    """
    update_artifacts = update_artifacts or set()
    chroma_url = chroma_url or config.CHROMA_URL
    sync_dir = sync_dir or _default_sync_dir()
    chroma_dir = Path(sync_dir) / CHROMA_SUBDIR

    domain_stats: dict[str, dict[str, int | str]] = {}
    # Batch POSTs that failed. Without this a restore in which every batch
    # errored returns total_added=0 and no error field — byte-identical to a
    # restore that had nothing to do. Same defect the export side shipped with.
    failed_domains: dict[str, str] = {}
    total_added = 0
    total_skipped = 0
    total_updated = 0

    for domain in config.DOMAINS:
        coll_name = config.collection_name(domain)
        src_path = str(chroma_dir / f"{coll_name}.jsonl")

        if not os.path.exists(src_path):
            logger.warning("No ChromaDB export file found for domain '%s': %s", domain, src_path)
            domain_stats[domain] = {"added": 0, "skipped": 0}
            continue

        added = 0
        skipped = 0
        updated = 0

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
            update_ids: list[str] = []
            update_metas: list[dict] = []

            def _flush_updates() -> None:
                nonlocal updated
                if not update_ids:
                    return
                try:
                    resp = httpx.post(
                        f"{_v2_collections_base(chroma_url)}/{collection_id}/update",
                        json={"ids": update_ids, "metadatas": update_metas},
                        timeout=120.0,
                    )
                    resp.raise_for_status()
                    updated += len(update_ids)
                except Exception as exc:
                    from core.utils.swallowed import log_swallowed_error
                    log_swallowed_error('app.sync.import_.chroma_update', exc)
                    failed_domains[domain] = str(exc)
                finally:
                    update_ids.clear()
                    update_metas.clear()

            def _flush_batch() -> int:
                nonlocal added
                if not batch_ids:
                    return 0
                try:
                    resp = httpx.post(
                        f"{_v2_collections_base(chroma_url)}/{collection_id}/add",
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
                except Exception as exc:
                    from core.utils.swallowed import log_swallowed_error
                    log_swallowed_error('app.sync.import_', exc)
                    logger.error("ChromaDB batch add failed for %s: %s", coll_name, exc)
                    failed_domains[domain] = str(exc)
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
                    meta = row.get("metadata") or {}
                    owner = str(meta.get("artifact_id") or "")
                    if (
                        owner in update_artifacts and chunk_artifact_id(str(chunk_id)) == owner
                        and not _is_forgotten_chunk(str(chunk_id), meta)
                    ):
                        update_ids.append(chunk_id)
                        update_metas.append({k: meta[k] for k in _ROW_VERSION_KEYS if k in meta})
                        if len(update_ids) >= CHROMA_BATCH_SIZE:
                            _flush_updates()
                        continue
                    skipped += 1
                    continue

                meta = row.get("metadata") or {}
                artifact_id = meta.get("artifact_id")
                if (
                    (artifact_id and _is_forgotten_artifact(str(artifact_id)))
                    or _is_forgotten_transcript(meta)
                    or _is_forgotten_verified_memory(str(chunk_id))
                    or _is_forgotten_chunk(str(chunk_id), meta)
                ):
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
            _flush_updates()

        except Exception as exc:
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error('app.sync.import_', exc)
            logger.error("ChromaDB import failed for domain '%s': %s", domain, exc)
            failed_domains[domain] = str(exc)
            domain_stats[domain] = {"added": added, "skipped": skipped, "updated": updated, "error": str(exc)}
            continue

        domain_stats[domain] = {"added": added, "skipped": skipped, "updated": updated}
        total_added += added
        total_skipped += skipped
        total_updated += updated
        logger.info(
            "ChromaDB import domain '%s': %d added, %d skipped", domain, added, skipped
        )

    if failed_domains:
        logger.error(
            "ChromaDB import completed with failures in %d domain(s): %s",
            len(failed_domains), ", ".join(sorted(failed_domains)),
        )
    logger.info(
        "ChromaDB import complete: %d total added, %d total skipped",
        total_added, total_skipped,
    )
    return {
        "domains": domain_stats,
        "total_added": total_added,
        "total_skipped": total_skipped,
        "total_updated": total_updated,
        "failed_domains": failed_domains,
    }


def _chroma_ensure_collection(chroma_url: str, collection_name: str) -> None:
    """Create a ChromaDB collection if it does not already exist."""
    base = _v2_collections_base(chroma_url)
    try:
        resp = httpx.get(f"{base}/{collection_name}", timeout=15.0)
        if resp.status_code == HTTPStatus.OK:
            return
        # ChromaDB 1.x returns 404 for non-existent (0.5 returned 400 — both
        # treated identically here so the create path triggers in either era).
        if resp.status_code in (400, 404):
            httpx.post(
                base,
                json={"name": collection_name},
                timeout=15.0,
            ).raise_for_status()
            logger.info("Created ChromaDB collection: %s", collection_name)
    except Exception as exc:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('app.sync.import_', exc)
        logger.warning("Could not ensure collection %s: %s", collection_name, exc)


def _chroma_get_collection_id(chroma_url: str, collection_name: str) -> str | None:
    """Return the UUID for a named ChromaDB collection, or None on failure."""
    try:
        resp = httpx.get(
            f"{_v2_collections_base(chroma_url)}/{collection_name}",
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json().get("id")
    except Exception as exc:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('app.sync.import_', exc)
        logger.warning("Cannot get ID for collection %s: %s", collection_name, exc)
        return None


def _chroma_get_all_ids(chroma_url: str, collection_id: str) -> set:
    """Retrieve all chunk IDs from a ChromaDB collection for deduplication."""
    ids: set = set()
    offset = 0
    while True:
        try:
            resp = httpx.post(
                f"{_v2_collections_base(chroma_url)}/{collection_id}/get",
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
        except Exception as exc:
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error('app.sync.import_', exc)
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

        # A domain new to this machine merges into an empty corpus, so its rows
        # pass the same forgotten-passage check as any other import.
        if not dst_file.exists():
            try:
                dst_file.touch()
            except OSError as exc:
                logger.warning("BM25 corpus create failed for %s: %s", src_file.name, exc)
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
                if cid and cid not in existing_ids and not _is_forgotten_lexical_row(str(cid)):
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

        except Exception as exc:
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error('app.sync.import_', exc)
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
                from core.utils.swallowed import log_swallowed_error
                log_swallowed_error('app.sync.import_', e)
                logger.debug("Skipping malformed Redis log entry during dedup: %s", e)
    except Exception as exc:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('app.sync.import_', exc)
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
        except Exception as exc:
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error('app.sync.import_', exc)
            logger.error("Redis LPUSH failed during import: %s", exc)
            return {"error": str(exc), "entries_added": entries_added, "entries_skipped": entries_skipped}

    logger.info(
        "Redis import complete: %d entries added, %d skipped", entries_added, entries_skipped
    )
    return {"entries_added": entries_added, "entries_skipped": entries_skipped}


def import_memories(driver, sync_dir: str | None = None) -> dict[str, Any]:
    """
    Merge :Memory nodes and their direct provenance edges (EXTRACTED_FROM →
    Conversation, RELATES_TO → Artifact) from {sync_dir}/neo4j/memories.jsonl
    and memory_edges.jsonl into the local graph.

    Idempotent: MERGE by id + `SET m += $props` (never a blind CREATE), so
    re-running an import never duplicates a memory.
    """
    sync_dir = sync_dir or _default_sync_dir()
    neo4j_dir = Path(sync_dir) / NEO4J_SUBDIR

    memories_merged = 0
    edges_merged = 0

    memories_path = str(neo4j_dir / MEMORIES_JSONL)
    try:
        with driver.session() as session:
            for row in _iter_jsonl(memories_path):
                memory_id = row.get("id")
                props = row.get("props")
                if not memory_id or not props:
                    continue
                if forget_registry.is_forgotten("memory", str(memory_id)):
                    continue
                try:
                    # The newer side wins, as for artifacts: a version this
                    # machine superseded since must not be reopened by an older export.
                    session.run(
                        "MERGE (m:Memory {id: $id}) WITH m "
                        "WHERE m.updated_at IS NULL OR $updated IS NULL OR $updated >= m.updated_at "
                        "SET m += $props",
                        id=memory_id,
                        props=_with_lineage_cleared(props),
                        updated=props.get("updated_at"),
                    )
                    memories_merged += 1
                except Exception as exc:
                    from core.utils.swallowed import log_swallowed_error
                    log_swallowed_error('app.sync.import_', exc)
                    logger.warning("Failed to merge Memory %s: %s", memory_id, exc)
    except Exception as exc:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('app.sync.import_', exc)
        logger.error("Memory import failed: %s", exc)
        return {"error": str(exc), "memories_merged": memories_merged, "edges_merged": edges_merged}

    edges_path = str(neo4j_dir / MEMORY_EDGES_JSONL)
    try:
        with driver.session() as session:
            for row in _iter_jsonl(edges_path):
                source_id = row.get("source_id")
                rel_type = row.get("rel_type")
                target_id = row.get("target_id")
                if not (source_id and rel_type and target_id):
                    continue
                try:
                    if rel_type == "EXTRACTED_FROM":
                        session.run(
                            "MATCH (m:Memory {id: $source_id}) "
                            "MERGE (c:Conversation {id: $target_id}) "
                            "MERGE (m)-[:EXTRACTED_FROM]->(c)",
                            source_id=source_id, target_id=target_id,
                        )
                        edges_merged += 1
                    elif rel_type == "RELATES_TO":
                        session.run(
                            "MATCH (m:Memory {id: $source_id}) "
                            "MATCH (a:Artifact {id: $target_id}) "
                            "MERGE (m)-[:RELATES_TO]->(a)",
                            source_id=source_id, target_id=target_id,
                        )
                        edges_merged += 1
                    else:
                        logger.warning("Skipping unknown memory edge rel_type: %s", rel_type)
                except Exception as exc:
                    from core.utils.swallowed import log_swallowed_error
                    log_swallowed_error('app.sync.import_', exc)
                    logger.warning(
                        "Failed to merge memory edge %s→%s (%s): %s",
                        source_id, target_id, rel_type, exc,
                    )
    except Exception as exc:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('app.sync.import_', exc)
        logger.error("Memory edge import failed: %s", exc)
        return {"error": str(exc), "memories_merged": memories_merged, "edges_merged": edges_merged}

    logger.info(
        "Memory import complete: %d memories, %d edges", memories_merged, edges_merged
    )
    return {"memories_merged": memories_merged, "edges_merged": edges_merged}


_IMPORT_FACT = """
MERGE (f:Fact {uid: $uid})
SET f += $props
WITH f
UNWIND (CASE WHEN size($subjects) = 0 THEN [null] ELSE $subjects END) AS sid
FOREACH (_ IN CASE WHEN sid IS NULL THEN [] ELSE [1] END |
  MERGE (s:Entity {canonical_id: sid})
  MERGE (s)-[:HAS_FACT]->(f)
)
WITH DISTINCT f
OPTIONAL MATCH (a:Artifact {id: $source})
FOREACH (_ IN CASE WHEN a IS NULL THEN [] ELSE [1] END | MERGE (a)-[:FACT]->(f))
WITH f
UNWIND (CASE WHEN size($objects) = 0 THEN [null] ELSE $objects END) AS oid
FOREACH (_ IN CASE WHEN oid IS NULL THEN [] ELSE [1] END |
  MERGE (o:Entity {canonical_id: oid})
  MERGE (f)-[:FACT_OBJECT]->(o)
)
"""


_FACT_CLOSURE = ("valid_to", "invalid_at", "closed_by")


def import_facts(driver, sync_dir: str | None = None, applied: set[str] | None = None) -> dict[str, Any]:
    """Merge :Fact versions from {sync_dir}/neo4j/facts.jsonl. A fact whose
    source memory is forgotten, or not on this machine, is skipped: a fact holds
    its memory's text and goes where the memory goes. Only facts of artifacts
    this import took from the other machine (``applied``, the newer side by
    ``updated_at``) are written, so a fact never disagrees with its version; a
    closure the row lacks is cleared, so a fact reopened there reopens here.
    Idempotent by uid."""
    sync_dir = sync_dir or _default_sync_dir()
    path = str(Path(sync_dir) / NEO4J_SUBDIR / FACTS_JSONL)
    merged = skipped = 0
    try:
        with driver.session() as session:
            for row in _iter_jsonl(path):
                raw = dict(row.get("props") or {})
                uid, source = raw.get("uid"), str(raw.get("source_artifact_id") or "")
                props = {**dict.fromkeys(_FACT_CLOSURE), **{k: raw[k] for k in FACT_PROPS if k in raw}}
                if not uid or not source or source not in (applied or set()) or _is_forgotten_artifact(source):
                    skipped += 1
                    continue
                if session.run("MATCH (a:Artifact {id: $id}) RETURN count(a) AS n", id=source).single()["n"] == 0:
                    skipped += 1
                    continue
                session.run(
                    _IMPORT_FACT, uid=uid, props=props, source=source,
                    subjects=[s for s in row.get("subjects") or [] if s],
                    objects=[o for o in row.get("objects") or [] if o],
                )
                merged += 1
    except Exception as exc:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('app.sync.import_.facts', exc)
        return {"error": str(exc), "facts_merged": merged, "facts_skipped": skipped}
    return {"facts_merged": merged, "facts_skipped": skipped}


def import_entities(driver, sync_dir: str | None = None) -> dict[str, Any]:
    """
    Merge :Entity nodes and their direct MENTIONS provenance edges (from
    Artifacts) from {sync_dir}/neo4j/entities.jsonl and entity_edges.jsonl
    into the local graph.

    Wiki pages are computed from entities at read time
    (app.services.wiki_pages) so they regenerate automatically after this
    import — they are never exported/imported directly. Derived
    entity-entity edges (CO_MENTIONED, SIMILAR_TO, IN_COMMUNITY) are also
    not imported here — they are recomputed by community_detection.py /
    semantic_edges.py, not restored from a backup.

    Idempotent: MERGE by canonical_id + `SET e += $props`.
    """
    sync_dir = sync_dir or _default_sync_dir()
    neo4j_dir = Path(sync_dir) / NEO4J_SUBDIR

    entities_merged = 0
    edges_merged = 0

    entities_path = str(neo4j_dir / ENTITIES_JSONL)
    try:
        with driver.session() as session:
            for row in _iter_jsonl(entities_path):
                canonical_id = row.get("canonical_id")
                props = row.get("props")
                if not canonical_id or not props:
                    continue
                try:
                    session.run(
                        "MERGE (e:Entity {canonical_id: $canonical_id}) SET e += $props",
                        canonical_id=canonical_id,
                        props=without_wiki_page(props),
                    )
                    entities_merged += 1
                except Exception as exc:
                    from core.utils.swallowed import log_swallowed_error
                    log_swallowed_error('app.sync.import_', exc)
                    logger.warning("Failed to merge Entity %s: %s", canonical_id, exc)
    except Exception as exc:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('app.sync.import_', exc)
        logger.error("Entity import failed: %s", exc)
        return {"error": str(exc), "entities_merged": entities_merged, "edges_merged": edges_merged}

    edges_path = str(neo4j_dir / ENTITY_EDGES_JSONL)
    try:
        with driver.session() as session:
            for row in _iter_jsonl(edges_path):
                source_id = row.get("source_id")
                target_id = row.get("target_id")
                rel_type = row.get("rel_type")
                props = row.get("props") or {}
                if not (source_id and target_id and rel_type == "MENTIONS"):
                    if rel_type and rel_type != "MENTIONS":
                        logger.warning("Skipping unknown entity edge rel_type: %s", rel_type)
                    continue
                try:
                    session.run(
                        "MATCH (a:Artifact {id: $source_id}) "
                        "MATCH (e:Entity {canonical_id: $target_id}) "
                        "MERGE (a)-[r:MENTIONS]->(e) "
                        "SET r += $props",
                        source_id=source_id, target_id=target_id, props=props,
                    )
                    edges_merged += 1
                except Exception as exc:
                    from core.utils.swallowed import log_swallowed_error
                    log_swallowed_error('app.sync.import_', exc)
                    logger.warning(
                        "Failed to merge MENTIONS edge %s→%s: %s", source_id, target_id, exc,
                    )
    except Exception as exc:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('app.sync.import_', exc)
        logger.error("Entity edge import failed: %s", exc)
        return {"error": str(exc), "entities_merged": entities_merged, "edges_merged": edges_merged}

    logger.info(
        "Entity import complete: %d entities, %d edges", entities_merged, edges_merged
    )
    return {"entities_merged": entities_merged, "edges_merged": edges_merged}


def import_conversations(sync_dir: str | None = None) -> dict[str, Any]:
    """
    Ensure conversations already present at {sync_dir}/user/conversations/
    are explicitly accounted for as part of import_all.

    Conversations are stored directly in the sync dir (no separate local
    store to restore into), so this reads them back and re-writes them
    idempotently via write_conversation — making them a first-class,
    explicitly-reported surface of import_all rather than an implicit
    side effect of the filesystem already having the files.
    """
    sync_dir = sync_dir or _default_sync_dir()

    try:
        conversations = read_conversations(sync_dir)
    except Exception as exc:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('app.sync.import_', exc)
        logger.error("Conversation import failed: %s", exc)
        return {"error": str(exc), "conversations": 0}

    restored = 0
    skipped = 0
    skipped_forgotten = 0
    for conv in conversations:
        if forget_registry.is_forgotten("conversation", str(conv.get("id") or "")):
            skipped_forgotten += 1
            continue
        try:
            write_conversation(sync_dir, conv)
            restored += 1
        except Exception as exc:
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error('app.sync.import_', exc)
            logger.warning("Skipping conversation during import: %s", exc)
            skipped += 1

    logger.info(
        "Conversation import complete: %d restored, %d skipped, %d forgotten",
        restored, skipped, skipped_forgotten,
    )
    return {"conversations": restored, "skipped": skipped, "skipped_forgotten": skipped_forgotten}


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
        from app.sync.manifest import read_manifest
        manifest = read_manifest(sync_dir)
        last_sync_at = manifest.get("last_exported_at")
    except (FileNotFoundError, ValueError):
        pass

    # Apply tombstones first (delete remote-deleted artifacts before importing new data)
    tombstone_result: dict[str, Any] = {"deleted": 0}
    try:
        from app.sync.tombstones import apply_tombstones
        tombstone_result = apply_tombstones(driver, chroma_url, sync_dir=sync_dir)
    except Exception as exc:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('app.sync.import_', exc)
        logger.warning("Tombstone application failed (non-blocking): %s", exc)

    neo4j_result = import_neo4j(
        driver, sync_dir=sync_dir, force=force,
        conflict_strategy=conflict_strategy, last_sync_at=last_sync_at,
    )
    applied = set(neo4j_result.pop("applied_ids", []) or [])
    chroma_result = import_chroma(
        chroma_url=chroma_url, sync_dir=sync_dir, force=force, update_artifacts=applied,
    )
    bm25_result = import_bm25(sync_dir=sync_dir)

    # Memories/entities depend on Artifacts already being imported above
    # (RELATES_TO / MENTIONS MATCH the artifact side); conversations have no
    # cross-surface dependency.
    memories_result = import_memories(driver, sync_dir=sync_dir)
    entities_result = import_entities(driver, sync_dir=sync_dir)
    facts_result = import_facts(driver, sync_dir=sync_dir, applied=applied)
    conversations_result = import_conversations(sync_dir=sync_dir)

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

        # Read the authoritative field rather than re-deriving it from the
        # per-domain "error" key: that key is written only by the outer
        # handler, so a domain whose batch POSTs all failed (the common
        # disk-full / Chroma-down case) never appeared here.
        chroma_domains = chroma_result.get("domains", {})
        failed_domains = sorted(
            set(chroma_result.get("failed_domains", {}))
            | {
                d for d, stats in chroma_domains.items()
                if isinstance(stats, dict) and "error" in stats
            }
        )
        if failed_domains:
            consistency_warnings.append(
                f"ChromaDB import failed for domains: {', '.join(failed_domains)}. "
                "Some domains may be missing chunks."
            )

        for warning in consistency_warnings:
            logger.warning("Consistency check: %s", warning)
    except Exception as exc:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('app.sync.import_', exc)
        logger.warning("Post-import consistency check failed: %s", exc)

    # Rebuild BM25 in-memory indexes after importing new corpus files
    bm25_chunks_added = bm25_result.get("chunks_added", 0) if isinstance(bm25_result, dict) else 0
    if bm25_chunks_added > 0:
        try:
            from core.retrieval.bm25 import rebuild_all as bm25_rebuild_all
            rebuilt = bm25_rebuild_all()
            logger.info("BM25 indexes rebuilt for %d domains after import", rebuilt)
        except Exception as exc:
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error('app.sync.import_', exc)
            logger.warning("BM25 index rebuild failed after import: %s", exc)

    # A machine that has not migrated exports positional chunk ids; re-key them
    # now so they land on the rows this machine already holds.
    rekey_result: dict[str, Any] = {}
    try:
        from app.services.chunk_id_migration import migrate_chunk_ids
        rekey_result = migrate_chunk_ids(neo4j=driver)
    except Exception as exc:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('app.sync.import_.chunk_id_migration', exc)

    # Another machine may still export memories and facts without lineages.
    lineage_result: dict[str, Any] = {}
    try:
        from app.services.lineage_migration import migrate_lineages
        lineage_result = migrate_lineages(neo4j=driver)
    except Exception as exc:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('app.sync.import_.lineage_migration', exc)

    logger.info("Full import complete from %s", sync_dir)
    return {
        "chunk_ids_rekeyed": rekey_result,
        "lineages": lineage_result,
        "neo4j": neo4j_result,
        "chroma": chroma_result,
        "bm25": bm25_result,
        "memories": memories_result,
        "entities": entities_result,
        "facts": facts_result,
        "conversations": conversations_result,
        "redis": redis_result,
        "tombstones": tombstone_result,
        "consistency_warnings": consistency_warnings,
    }
