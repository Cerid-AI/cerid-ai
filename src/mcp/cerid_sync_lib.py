"""
Cerid AI Sync Library — cross-machine knowledge base export/import via Dropbox.

All export functions are read-only and safe to run while containers are live.
All import functions are non-destructive by default (force=False skips conflicts).

Usage (export):
    from cerid_sync_lib import export_all
    export_all(driver, chroma_url, redis_client, sync_dir)

Usage (import):
    from cerid_sync_lib import import_all
    import_all(driver, chroma_url, redis_client, sync_dir)

Usage (status):
    from cerid_sync_lib import compare_status
    status = compare_status(driver, chroma_url, redis_client, sync_dir)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

import config

logger = logging.getLogger("ai-companion.sync")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MANIFEST_FILENAME = "manifest.json"
ARTIFACTS_JSONL = "artifacts.jsonl"
DOMAINS_JSONL = "domains.jsonl"
RELATIONSHIPS_JSONL = "relationships.jsonl"
AUDIT_LOG_JSONL = "audit_log.jsonl"

NEO4J_SUBDIR = "neo4j"
CHROMA_SUBDIR = "chroma"
BM25_SUBDIR = "bm25"
REDIS_SUBDIR = "redis"

CHROMA_BATCH_SIZE = 500

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_sync_dir() -> str:
    """Return SYNC_DIR from config if present, else ~/Dropbox/cerid-sync."""
    return getattr(config, "SYNC_DIR", os.path.expanduser("~/Dropbox/cerid-sync"))


def _ensure_dir(path: str) -> Path:
    """Create directory (and parents) if it does not exist. Returns Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sha256_file(filepath: str) -> str:
    """Return hex SHA-256 of a file's contents."""
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError as exc:
        logger.warning("Cannot checksum %s: %s", filepath, exc)
        return ""


def _count_jsonl_lines(filepath: str) -> int:
    """Return number of non-empty lines in a JSONL file."""
    try:
        count = 0
        with open(filepath, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    count += 1
        return count
    except OSError:
        return 0


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_jsonl(filepath: str, rows: List[Dict[str, Any]]) -> int:
    """Write a list of dicts to a JSONL file. Returns number of rows written."""
    written = 0
    with open(filepath, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str) + "\n")
            written += 1
    return written


def _iter_jsonl(filepath: str):
    """Yield parsed dicts from a JSONL file, skipping blank/invalid lines."""
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning("Skipping malformed JSONL line %d in %s: %s", lineno, filepath, exc)
    except OSError as exc:
        logger.warning("Cannot read %s: %s", filepath, exc)


# ---------------------------------------------------------------------------
# Export — Neo4j
# ---------------------------------------------------------------------------

def export_neo4j(driver, sync_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Export all Artifact nodes, Domain nodes, and inter-artifact relationships
    from Neo4j to JSONL files under {sync_dir}/neo4j/.

    Files written:
        artifacts.jsonl      — one Artifact node per line
        domains.jsonl        — one Domain node per line
        relationships.jsonl  — one relationship per line

    Returns:
        {
            "artifacts": <count>,
            "domains": <count>,
            "relationships": <count>,
            "output_dir": "<path>",
        }
    """
    sync_dir = sync_dir or _default_sync_dir()
    out_dir = _ensure_dir(os.path.join(sync_dir, NEO4J_SUBDIR))

    artifacts: List[Dict[str, Any]] = []
    domains: List[Dict[str, Any]] = []
    relationships: List[Dict[str, Any]] = []

    try:
        with driver.session() as session:
            # --- Artifact nodes ---
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

            # --- Domain nodes ---
            result = session.run(
                "MATCH (d:Domain) RETURN d.name AS name ORDER BY d.name"
            )
            for record in result:
                domains.append(dict(record))

            # --- Relationships between Artifact nodes ---
            # BELONGS_TO (artifact → domain) is reconstructed from artifact.domain on import;
            # we export the richer inter-artifact relationships explicitly.
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


# ---------------------------------------------------------------------------
# Export — ChromaDB
# ---------------------------------------------------------------------------

def export_chroma(chroma_url: Optional[str] = None, sync_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Export all ChromaDB collections (one per domain) to per-domain JSONL files
    under {sync_dir}/chroma/.

    Each JSONL line contains: id, document, metadata, embedding (list[float]).
    Collections are fetched in batches of CHROMA_BATCH_SIZE.

    Returns:
        {
            "domains": {<domain>: <chunk_count>, ...},
            "total_chunks": <int>,
            "output_dir": "<path>",
        }
    """
    chroma_url = chroma_url or config.CHROMA_URL
    sync_dir = sync_dir or _default_sync_dir()
    out_dir = _ensure_dir(os.path.join(sync_dir, CHROMA_SUBDIR))

    domain_counts: Dict[str, int] = {}
    total_chunks = 0

    for domain in config.DOMAINS:
        collection_name = f"domain_{domain}"
        out_path = str(out_dir / f"domain_{domain}.jsonl")
        chunk_count = 0

        try:
            # Retrieve collection metadata to get total count
            coll_resp = httpx.get(
                f"{chroma_url}/api/v1/collections/{collection_name}",
                timeout=30.0,
            )
            if coll_resp.status_code == 404:
                logger.warning("ChromaDB collection %s not found — skipping", collection_name)
                domain_counts[domain] = 0
                # Write empty file so manifest can still checksum it
                _write_jsonl(out_path, [])
                continue
            coll_resp.raise_for_status()
            coll_data = coll_resp.json()
            collection_id = coll_data.get("id", collection_name)

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
            logger.error("ChromaDB HTTP error for %s: %s", collection_name, exc)
            domain_counts[domain] = chunk_count
            continue
        except Exception as exc:
            logger.error("ChromaDB export failed for %s: %s", collection_name, exc)
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


# ---------------------------------------------------------------------------
# Export — BM25
# ---------------------------------------------------------------------------

def export_bm25(sync_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Copy BM25 JSONL corpus files from config.BM25_DATA_DIR to {sync_dir}/bm25/.

    Only .jsonl files are copied. Existing destination files are overwritten.

    Returns:
        {
            "files_copied": <int>,
            "files_skipped": <int>,
            "output_dir": "<path>",
        }
    """
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


# ---------------------------------------------------------------------------
# Export — Redis
# ---------------------------------------------------------------------------

def export_redis(redis_client, sync_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Dump the full Redis ingest:log list to {sync_dir}/redis/audit_log.jsonl.

    Each entry is already a JSON string in Redis; we decode and re-emit one
    per line for a canonical JSONL format.

    Returns:
        {
            "entries_exported": <int>,
            "output_dir": "<path>",
        }
    """
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


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def write_manifest(
    sync_dir: Optional[str] = None,
    machine_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Write manifest.json to sync_dir root with:
        - machine_id (defaults to hostname)
        - timestamp (UTC ISO-8601)
        - per-file entry counts
        - per-file SHA-256 checksums

    Returns the manifest dict.
    """
    sync_dir = sync_dir or _default_sync_dir()
    sync_path = Path(sync_dir)

    if machine_id is None:
        import socket
        machine_id = socket.gethostname()

    # Enumerate all tracked files across subdirs
    tracked_files: List[Tuple[str, str]] = [
        # (relative path within sync_dir, absolute path)
        (f"{NEO4J_SUBDIR}/{ARTIFACTS_JSONL}",    str(sync_path / NEO4J_SUBDIR / ARTIFACTS_JSONL)),
        (f"{NEO4J_SUBDIR}/{DOMAINS_JSONL}",       str(sync_path / NEO4J_SUBDIR / DOMAINS_JSONL)),
        (f"{NEO4J_SUBDIR}/{RELATIONSHIPS_JSONL}", str(sync_path / NEO4J_SUBDIR / RELATIONSHIPS_JSONL)),
        (f"{REDIS_SUBDIR}/{AUDIT_LOG_JSONL}",     str(sync_path / REDIS_SUBDIR / AUDIT_LOG_JSONL)),
    ]

    # Add per-domain Chroma files
    for domain in config.DOMAINS:
        rel = f"{CHROMA_SUBDIR}/domain_{domain}.jsonl"
        tracked_files.append((rel, str(sync_path / CHROMA_SUBDIR / f"domain_{domain}.jsonl")))

    # Add BM25 files discovered on disk
    bm25_src = sync_path / BM25_SUBDIR
    if bm25_src.exists():
        for f in sorted(bm25_src.glob("*.jsonl")):
            rel = f"{BM25_SUBDIR}/{f.name}"
            tracked_files.append((rel, str(f)))

    file_entries: Dict[str, Dict[str, Any]] = {}
    for rel_path, abs_path in tracked_files:
        if not os.path.exists(abs_path):
            file_entries[rel_path] = {"exists": False, "count": 0, "sha256": ""}
            continue
        count = _count_jsonl_lines(abs_path) if abs_path.endswith(".jsonl") else None
        checksum = _sha256_file(abs_path)
        entry: Dict[str, Any] = {"exists": True, "sha256": checksum}
        if count is not None:
            entry["count"] = count
        file_entries[rel_path] = entry

    manifest = {
        "machine_id": machine_id,
        "timestamp": _utcnow_iso(),
        "sync_format_version": 1,
        "domains": config.DOMAINS,
        "files": file_entries,
    }

    manifest_path = sync_path / MANIFEST_FILENAME
    with open(str(manifest_path), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, default=str)

    logger.info("Manifest written: machine=%s, %d files tracked → %s", machine_id, len(file_entries), manifest_path)
    return manifest


def read_manifest(sync_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Read and parse manifest.json from sync_dir.

    Returns the manifest dict, or raises FileNotFoundError if absent.
    Raises ValueError if the manifest is malformed.
    """
    sync_dir = sync_dir or _default_sync_dir()
    manifest_path = Path(sync_dir) / MANIFEST_FILENAME

    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest found at {manifest_path}")

    with open(str(manifest_path), "r", encoding="utf-8") as fh:
        try:
            manifest = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed manifest.json: {exc}") from exc

    required_keys = {"machine_id", "timestamp", "files"}
    missing = required_keys - set(manifest.keys())
    if missing:
        raise ValueError(f"manifest.json missing required keys: {missing}")

    return manifest


# ---------------------------------------------------------------------------
# Import — Neo4j
# ---------------------------------------------------------------------------

def import_neo4j(
    driver,
    sync_dir: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Merge Neo4j data from sync_dir into the local graph.

    Domain nodes:       MERGE by name (always safe).
    Artifact nodes:     MERGE by id. If the remote ingested_at is newer (or force=True),
                        all properties are updated. Relationships follow the artifact.
    Relationships:      MERGE by (source_id, target_id, rel_type). Properties set on CREATE only.

    Returns:
        {
            "domains_merged": <int>,
            "artifacts_created": <int>,
            "artifacts_updated": <int>,
            "artifacts_skipped": <int>,
            "relationships_merged": <int>,
        }
    """
    sync_dir = sync_dir or _default_sync_dir()
    neo4j_dir = Path(sync_dir) / NEO4J_SUBDIR

    domains_merged = 0
    artifacts_created = 0
    artifacts_updated = 0
    artifacts_skipped = 0
    relationships_merged = 0

    # --- Domains ---
    domains_path = str(neo4j_dir / DOMAINS_JSONL)
    for row in _iter_jsonl(domains_path):
        name = row.get("name")
        if not name:
            continue
        try:
            with driver.session() as session:
                session.run("MERGE (:Domain {name: $name})", name=name)
            domains_merged += 1
        except Exception as exc:
            logger.warning("Failed to merge Domain '%s': %s", name, exc)

    # --- Artifacts ---
    artifacts_path = str(neo4j_dir / ARTIFACTS_JSONL)
    for row in _iter_jsonl(artifacts_path):
        artifact_id = row.get("id")
        if not artifact_id:
            continue

        try:
            with driver.session() as session:
                # Check if artifact exists locally and compare timestamps
                existing = session.run(
                    "MATCH (a:Artifact {id: $id}) RETURN a.ingested_at AS ingested_at",
                    id=artifact_id,
                ).single()

                remote_ingested_at = row.get("ingested_at") or ""
                local_ingested_at = existing["ingested_at"] if existing else None

                should_update = (
                    force
                    or local_ingested_at is None
                    or remote_ingested_at > local_ingested_at  # ISO-8601 lexicographic compare
                )

                if local_ingested_at is None:
                    # New artifact — create it
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
                            recategorized_at: $recategorized_at
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
                    )
                    artifacts_created += 1

                elif should_update:
                    # Existing artifact that is stale locally — update properties only
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
                            a.recategorized_at = $recategorized_at
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
                    )
                    artifacts_updated += 1

                else:
                    artifacts_skipped += 1

        except Exception as exc:
            logger.warning("Failed to import artifact %s: %s", artifact_id[:8], exc)

    # --- Relationships ---
    relationships_path = str(neo4j_dir / RELATIONSHIPS_JSONL)
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
                "created_at": row.get("created_at") or _utcnow_iso(),
            }
            # Remove None values so we don't overwrite with nulls
            props = {k: v for k, v in props.items() if v is not None}

            cypher = (
                f"MATCH (s:Artifact {{id: $source_id}}), (t:Artifact {{id: $target_id}}) "
                f"MERGE (s)-[r:{rel_type}]->(t) "
                f"ON CREATE SET r += $props "
                f"RETURN r IS NOT NULL AS ok"
            )
            with driver.session() as session:
                session.run(cypher, source_id=source_id, target_id=target_id, props=props)
            relationships_merged += 1
        except Exception as exc:
            logger.warning(
                "Failed to merge relationship %s→%s (%s): %s",
                source_id[:8], target_id[:8], rel_type, exc,
            )

    logger.info(
        "Neo4j import complete: %d domains, %d artifacts created, %d updated, "
        "%d skipped, %d relationships",
        domains_merged, artifacts_created, artifacts_updated, artifacts_skipped, relationships_merged,
    )
    return {
        "domains_merged": domains_merged,
        "artifacts_created": artifacts_created,
        "artifacts_updated": artifacts_updated,
        "artifacts_skipped": artifacts_skipped,
        "relationships_merged": relationships_merged,
    }


# ---------------------------------------------------------------------------
# Import — ChromaDB
# ---------------------------------------------------------------------------

def import_chroma(
    chroma_url: Optional[str] = None,
    sync_dir: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Merge ChromaDB chunks from sync JSONL files into the local ChromaDB instance.

    For each domain:
        - Fetch existing IDs from the local collection (GET /ids)
        - Skip chunks whose IDs already exist (unless force=True)
        - Add new chunks in batches with pre-computed embeddings

    Returns:
        {
            "domains": {<domain>: {"added": <int>, "skipped": <int>}, ...},
            "total_added": <int>,
            "total_skipped": <int>,
        }
    """
    chroma_url = chroma_url or config.CHROMA_URL
    sync_dir = sync_dir or _default_sync_dir()
    chroma_dir = Path(sync_dir) / CHROMA_SUBDIR

    domain_stats: Dict[str, Dict[str, int]] = {}
    total_added = 0
    total_skipped = 0

    for domain in config.DOMAINS:
        collection_name = f"domain_{domain}"
        src_path = str(chroma_dir / f"domain_{domain}.jsonl")

        if not os.path.exists(src_path):
            logger.warning("No ChromaDB export file found for domain '%s': %s", domain, src_path)
            domain_stats[domain] = {"added": 0, "skipped": 0}
            continue

        added = 0
        skipped = 0

        try:
            # Ensure the collection exists (create if absent)
            _chroma_ensure_collection(chroma_url, collection_name)

            # Fetch collection ID (needed for data endpoint)
            collection_id = _chroma_get_collection_id(chroma_url, collection_name)
            if not collection_id:
                logger.error("Cannot resolve collection ID for %s — skipping", collection_name)
                domain_stats[domain] = {"added": 0, "skipped": 0}
                continue

            # Retrieve existing chunk IDs to avoid duplicates
            existing_ids: set = set()
            if not force:
                existing_ids = _chroma_get_all_ids(chroma_url, collection_id)

            # Buffer for batched upserts
            batch_ids: List[str] = []
            batch_docs: List[str] = []
            batch_metas: List[Dict] = []
            batch_embs: List[List[float]] = []

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
                except Exception as exc:
                    logger.error("ChromaDB batch add failed for %s: %s", collection_name, exc)
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
                    # Chunk without embedding cannot be added to ChromaDB usefully;
                    # skip and log so the operator can investigate.
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

        except Exception as exc:
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
        if resp.status_code == 404:
            httpx.post(
                f"{chroma_url}/api/v1/collections",
                json={"name": collection_name},
                timeout=15.0,
            ).raise_for_status()
            logger.info("Created ChromaDB collection: %s", collection_name)
    except Exception as exc:
        logger.warning("Could not ensure collection %s: %s", collection_name, exc)


def _chroma_get_collection_id(chroma_url: str, collection_name: str) -> Optional[str]:
    """Return the UUID for a named ChromaDB collection, or None on failure."""
    try:
        resp = httpx.get(
            f"{chroma_url}/api/v1/collections/{collection_name}",
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json().get("id")
    except Exception as exc:
        logger.warning("Cannot get ID for collection %s: %s", collection_name, exc)
        return None


def _chroma_get_all_ids(chroma_url: str, collection_id: str) -> set:
    """
    Retrieve all chunk IDs from a ChromaDB collection for deduplication.
    Uses paginated GET requests. Returns a set of ID strings.
    """
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
            batch_ids: List[str] = resp.json().get("ids", [])
            if not batch_ids:
                break
            ids.update(batch_ids)
            offset += len(batch_ids)
            if len(batch_ids) < CHROMA_BATCH_SIZE:
                break
        except Exception as exc:
            logger.warning("Error fetching existing IDs at offset %d: %s", offset, exc)
            break
    return ids


# ---------------------------------------------------------------------------
# Import — BM25
# ---------------------------------------------------------------------------

def import_bm25(sync_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Merge BM25 corpus files from {sync_dir}/bm25/ into config.BM25_DATA_DIR.

    For each source .jsonl file:
        - If the destination file does not exist, copy it directly.
        - If the destination file exists, merge by chunk_id: add rows whose
          chunk_id is not already present.

    Returns:
        {
            "files_processed": <int>,
            "chunks_added": <int>,
            "chunks_skipped": <int>,
        }
    """
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
            # No local copy — copy directly
            try:
                shutil.copy2(str(src_file), str(dst_file))
                line_count = _count_jsonl_lines(str(src_file))
                chunks_added += line_count
                files_processed += 1
                logger.debug("BM25 copied new corpus file: %s (%d chunks)", src_file.name, line_count)
            except OSError as exc:
                logger.warning("BM25 copy failed for %s: %s", src_file.name, exc)
            continue

        # Merge by chunk_id
        try:
            existing_ids: set = set()
            for row in _iter_jsonl(str(dst_file)):
                cid = row.get("chunk_id") or row.get("id")
                if cid:
                    existing_ids.add(cid)

            new_rows: List[Dict[str, Any]] = []
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

        except Exception as exc:
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


# ---------------------------------------------------------------------------
# Import — Redis
# ---------------------------------------------------------------------------

def import_redis(
    redis_client,
    sync_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Append audit log entries from {sync_dir}/redis/audit_log.jsonl into the
    local Redis ingest:log list, skipping entries already present.

    Deduplication key: (artifact_id, timestamp). Entries are compared by
    building a local set of those composite keys from the live Redis list,
    then only appending entries absent from that set.

    Returns:
        {
            "entries_added": <int>,
            "entries_skipped": <int>,
        }
    """
    sync_dir = sync_dir or _default_sync_dir()
    src_path = str(Path(sync_dir) / REDIS_SUBDIR / AUDIT_LOG_JSONL)

    entries_added = 0
    entries_skipped = 0

    if not os.path.exists(src_path):
        logger.warning("Redis audit log export not found: %s — skipping import", src_path)
        return {"entries_added": 0, "entries_skipped": 0}

    # Build deduplication set from current Redis list
    existing_keys: set = set()
    try:
        raw_existing = redis_client.lrange(config.REDIS_INGEST_LOG, 0, -1)
        for raw in raw_existing:
            try:
                entry = json.loads(raw)
                key = (entry.get("artifact_id", ""), entry.get("timestamp", ""))
                existing_keys.add(key)
            except json.JSONDecodeError:
                pass
    except Exception as exc:
        logger.error("Cannot read existing Redis log for dedup: %s", exc)
        return {"error": str(exc), "entries_added": 0, "entries_skipped": 0}

    # Read sync file (chronological order) and append newer entries
    # We push to the RIGHT of the list so that LPUSH order is preserved:
    # the native Redis list is newest-first, so we push to the tail
    # (using RPUSH would be wrong — use LPUSH with reversed order or RPUSH then LTRIM).
    # Strategy: collect new rows, then LPUSH them in reverse so list order is maintained.
    new_entries: List[str] = []
    for row in _iter_jsonl(src_path):
        key = (row.get("artifact_id", ""), row.get("timestamp", ""))
        if key in existing_keys:
            entries_skipped += 1
            continue
        new_entries.append(json.dumps(row, default=str))
        entries_added += 1

    if new_entries:
        try:
            # Push in reverse order so the first entry ends up at position 0 (LPUSH reverses)
            for entry_str in reversed(new_entries):
                redis_client.lpush(config.REDIS_INGEST_LOG, entry_str)
            # Trim to maintain max log size
            redis_client.ltrim(config.REDIS_INGEST_LOG, 0, config.REDIS_LOG_MAX - 1)
        except Exception as exc:
            logger.error("Redis LPUSH failed during import: %s", exc)
            return {"error": str(exc), "entries_added": entries_added, "entries_skipped": entries_skipped}

    logger.info(
        "Redis import complete: %d entries added, %d skipped", entries_added, entries_skipped
    )
    return {"entries_added": entries_added, "entries_skipped": entries_skipped}


# ---------------------------------------------------------------------------
# Status comparison
# ---------------------------------------------------------------------------

def compare_status(
    driver,
    chroma_url: Optional[str] = None,
    redis_client=None,
    sync_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compare local database counts against the counts recorded in the sync manifest.

    Returns a dict with:
        {
            "sync_dir": "<path>",
            "manifest": {<manifest contents>} | None,
            "local": {
                "neo4j_artifacts": <int>,
                "neo4j_domains": <int>,
                "neo4j_relationships": <int>,
                "chroma_chunks": {<domain>: <int>, ...},
                "redis_entries": <int>,
            },
            "sync": {
                "neo4j_artifacts": <int>,
                "neo4j_domains": <int>,
                "neo4j_relationships": <int>,
                "chroma_chunks": {<domain>: <int>, ...},
                "redis_entries": <int>,
            },
            "diff": {
                "neo4j_artifacts": <local - sync>,
                ...
            }
        }
    """
    chroma_url = chroma_url or config.CHROMA_URL
    sync_dir = sync_dir or _default_sync_dir()

    # --- Local counts ---
    local: Dict[str, Any] = {
        "neo4j_artifacts": 0,
        "neo4j_domains": 0,
        "neo4j_relationships": 0,
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
    except Exception as exc:
        logger.warning("Neo4j local count failed: %s", exc)

    for domain in config.DOMAINS:
        collection_name = f"domain_{domain}"
        try:
            coll_resp = httpx.get(
                f"{chroma_url}/api/v1/collections/{collection_name}", timeout=10.0
            )
            if coll_resp.status_code == 200:
                coll_id = coll_resp.json().get("id", collection_name)
                count_resp = httpx.get(
                    f"{chroma_url}/api/v1/collections/{coll_id}/count", timeout=10.0
                )
                if count_resp.status_code == 200:
                    local["chroma_chunks"][domain] = count_resp.json()
                else:
                    local["chroma_chunks"][domain] = 0
            else:
                local["chroma_chunks"][domain] = 0
        except Exception as exc:
            logger.warning("ChromaDB local count failed for %s: %s", domain, exc)
            local["chroma_chunks"][domain] = 0

    if redis_client is not None:
        try:
            local["redis_entries"] = redis_client.llen(config.REDIS_INGEST_LOG)
        except Exception as exc:
            logger.warning("Redis local count failed: %s", exc)

    # --- Sync counts (from manifest + JSONL line counts) ---
    sync: Dict[str, Any] = {
        "neo4j_artifacts": 0,
        "neo4j_domains": 0,
        "neo4j_relationships": 0,
        "chroma_chunks": {d: 0 for d in config.DOMAINS},
        "redis_entries": 0,
    }
    manifest = None

    sync_path = Path(sync_dir)
    try:
        manifest = read_manifest(sync_dir)
        files = manifest.get("files", {})

        def _manifest_count(rel: str) -> int:
            return files.get(rel, {}).get("count", 0)

        sync["neo4j_artifacts"] = _manifest_count(f"{NEO4J_SUBDIR}/{ARTIFACTS_JSONL}")
        sync["neo4j_domains"] = _manifest_count(f"{NEO4J_SUBDIR}/{DOMAINS_JSONL}")
        sync["neo4j_relationships"] = _manifest_count(f"{NEO4J_SUBDIR}/{RELATIONSHIPS_JSONL}")
        sync["redis_entries"] = _manifest_count(f"{REDIS_SUBDIR}/{AUDIT_LOG_JSONL}")

        for domain in config.DOMAINS:
            rel = f"{CHROMA_SUBDIR}/domain_{domain}.jsonl"
            sync["chroma_chunks"][domain] = _manifest_count(rel)

    except FileNotFoundError:
        logger.warning("No manifest found at %s — sync counts will be 0", sync_dir)
    except ValueError as exc:
        logger.warning("Manifest parse error: %s", exc)

    # --- Diff ---
    diff: Dict[str, Any] = {}
    for key in ("neo4j_artifacts", "neo4j_domains", "neo4j_relationships", "redis_entries"):
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


# ---------------------------------------------------------------------------
# Convenience wrappers — full export / import
# ---------------------------------------------------------------------------

def export_all(
    driver,
    chroma_url: Optional[str] = None,
    redis_client=None,
    sync_dir: Optional[str] = None,
    machine_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run all export steps in sequence and write a manifest.

    Steps: export_neo4j → export_chroma → export_bm25 → export_redis → write_manifest

    Returns a dict with results from each step plus the manifest.
    """
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


def import_all(
    driver,
    chroma_url: Optional[str] = None,
    redis_client=None,
    sync_dir: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Run all import steps in sequence.

    Steps: import_neo4j → import_chroma → import_bm25 → import_redis

    Returns a dict with results from each step.
    """
    chroma_url = chroma_url or config.CHROMA_URL
    sync_dir = sync_dir or _default_sync_dir()

    logger.info("Starting full import from %s (force=%s)", sync_dir, force)

    neo4j_result = import_neo4j(driver, sync_dir=sync_dir, force=force)
    chroma_result = import_chroma(chroma_url=chroma_url, sync_dir=sync_dir, force=force)
    bm25_result = import_bm25(sync_dir=sync_dir)

    redis_result: Dict[str, Any] = {"entries_added": 0, "skipped": True}
    if redis_client is not None:
        redis_result = import_redis(redis_client, sync_dir=sync_dir)
    else:
        logger.warning("No Redis client provided — skipping Redis import")

    logger.info("Full import complete from %s", sync_dir)
    return {
        "neo4j": neo4j_result,
        "chroma": chroma_result,
        "bm25": bm25_result,
        "redis": redis_result,
    }
