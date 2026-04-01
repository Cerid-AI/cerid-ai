# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Storage metrics and ingestion history endpoints (Phase 56).

Endpoints:
  /system/storage         — Aggregated storage usage across all data stores
  /admin/ingest-history   — Persistent ingestion activity feed from Redis stream

Dependencies: deps.py (service connections), config/settings.py (thresholds)
"""
from __future__ import annotations

import json
import logging
import os
import time

from fastapi import APIRouter, Query

from config.settings import (
    BM25_DATA_DIR,
    INGEST_HISTORY_RETENTION_DAYS,
    STORAGE_CRITICAL_PCT,
    STORAGE_LIMIT_MB,
    STORAGE_WARN_PCT,
)
from deps import get_chroma, get_neo4j, get_redis
from errors import CeridError

router = APIRouter()
logger = logging.getLogger("ai-companion")

# Redis cache key + TTL for storage metrics
_STORAGE_CACHE_KEY = "cerid:system:storage_metrics"
_STORAGE_CACHE_TTL = 60  # seconds

# Redis stream key for ingestion history
INGEST_HISTORY_STREAM = "cerid:ingest:history"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _dir_size_mb(path: str) -> float:
    """Walk a directory and sum file sizes. Returns MB."""
    total = 0
    if not os.path.isdir(path):
        return 0.0
    for dirpath, _dirnames, filenames in os.walk(path):
        for f in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass  # File stat: skip inaccessible files
    return round(total / (1024 * 1024), 2)


def _chromadb_metrics() -> dict:
    """ChromaDB: collection count, total chunks, disk size."""
    try:
        client = get_chroma()
        collections = client.list_collections()
        total_chunks = 0
        for coll in collections:
            try:
                total_chunks += coll.count()
            except (CeridError, ValueError, OSError, RuntimeError):
                pass  # Collection count: skip unavailable collections
        # Disk size: ChromaDB persist directory inside the container
        chroma_dir = os.getenv("CHROMA_PERSIST_DIR", "/chroma/chroma")
        disk_mb = _dir_size_mb(chroma_dir)
        return {
            "disk_mb": disk_mb,
            "collections": len(collections),
            "chunks": total_chunks,
        }
    except (CeridError, ValueError, OSError, RuntimeError) as e:
        logger.warning("ChromaDB metrics unavailable: %s", e)
        return {"disk_mb": 0, "collections": 0, "chunks": 0, "error": str(e)}


def _neo4j_metrics() -> dict:
    """Neo4j: node count, relationship count."""
    driver = get_neo4j()
    if driver is None:
        return {"disk_mb": 0, "nodes": 0, "relationships": 0, "status": "disabled"}
    try:
        with driver.session() as session:
            nodes = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rels = session.run("MATCH ()-[r]-() RETURN count(r) AS c").single()["c"]
        return {"disk_mb": 0, "nodes": nodes, "relationships": rels}
    except (CeridError, ValueError, OSError, RuntimeError) as e:
        logger.warning("Neo4j metrics unavailable: %s", e)
        return {"disk_mb": 0, "nodes": 0, "relationships": 0, "error": str(e)}


def _redis_metrics() -> dict:
    """Redis: memory usage, peak memory, key count."""
    try:
        r = get_redis()
        info = r.info("memory")
        used = round(info.get("used_memory", 0) / (1024 * 1024), 2)
        peak = round(info.get("used_memory_peak", 0) / (1024 * 1024), 2)
        keys = r.dbsize()
        return {"memory_mb": used, "peak_mb": peak, "keys": keys}
    except (CeridError, ValueError, OSError, RuntimeError) as e:
        logger.warning("Redis metrics unavailable: %s", e)
        return {"memory_mb": 0, "peak_mb": 0, "keys": 0, "error": str(e)}


def _bm25_metrics() -> dict:
    """BM25: disk size of index directory, index file count."""
    bm25_dir = BM25_DATA_DIR
    disk_mb = _dir_size_mb(bm25_dir)
    index_count = 0
    if os.path.isdir(bm25_dir):
        index_count = sum(
            1 for f in os.listdir(bm25_dir)
            if os.path.isfile(os.path.join(bm25_dir, f))
        )
    return {"disk_mb": disk_mb, "index_count": index_count}


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/system/storage")
def get_storage_metrics():
    """Return storage usage across all data stores, cached for 60 seconds."""
    try:
        r = get_redis()
        cached = r.get(_STORAGE_CACHE_KEY)
        if cached:
            return json.loads(cached)
    except (CeridError, ValueError, OSError, RuntimeError):
        pass  # Storage cache: compute fresh on miss

    chromadb = _chromadb_metrics()
    neo4j = _neo4j_metrics()
    redis_m = _redis_metrics()
    bm25 = _bm25_metrics()

    total_mb = round(
        chromadb.get("disk_mb", 0)
        + neo4j.get("disk_mb", 0)
        + redis_m.get("memory_mb", 0)
        + bm25.get("disk_mb", 0),
        2,
    )
    pct = round((total_mb / STORAGE_LIMIT_MB) * 100, 1) if STORAGE_LIMIT_MB > 0 else 0

    result = {
        "chromadb": chromadb,
        "neo4j": neo4j,
        "redis": redis_m,
        "bm25": bm25,
        "total_mb": total_mb,
        "limit_mb": STORAGE_LIMIT_MB,
        "usage_pct": pct,
        "warn_pct": STORAGE_WARN_PCT,
        "critical_pct": STORAGE_CRITICAL_PCT,
        "status": (
            "critical" if pct >= STORAGE_CRITICAL_PCT
            else "warning" if pct >= STORAGE_WARN_PCT
            else "healthy"
        ),
        "timestamp": time.time(),
    }

    # Cache in Redis for 60 seconds
    try:
        r = get_redis()
        r.setex(_STORAGE_CACHE_KEY, _STORAGE_CACHE_TTL, json.dumps(result))
    except (CeridError, ValueError, OSError, RuntimeError):
        pass  # Storage cache: best-effort write

    return result


@router.get("/admin/ingest-history")
def get_ingest_history(
    limit: int = Query(50, ge=1, le=500),
    offset: str = Query("0-0", description="Redis stream ID for cursor-based pagination"),
):
    """Return recent ingestion events from Redis stream."""
    try:
        r = get_redis()
        # XREVRANGE returns newest-first.  Use '+' as start (newest), offset as end.
        if offset == "0-0":
            entries = r.xrevrange(INGEST_HISTORY_STREAM, "+", "-", count=limit)
        else:
            # Paginate: get entries older than the provided cursor
            entries = r.xrevrange(INGEST_HISTORY_STREAM, offset, "-", count=limit + 1)
            # Skip the first entry (it matches the cursor exactly)
            if entries and entries[0][0] == offset:
                entries = entries[1:]
            entries = entries[:limit]
    except (CeridError, ValueError, OSError, RuntimeError) as e:
        logger.warning("Ingest history unavailable: %s", e)
        return {"items": [], "total": 0, "next_cursor": None, "error": str(e)}

    items = []
    for entry_id, fields in entries:
        items.append({
            "id": entry_id,
            "filename": fields.get("filename", ""),
            "source_type": fields.get("source_type", "upload"),
            "domain": fields.get("domain", ""),
            "status": fields.get("status", "success"),
            "timestamp": fields.get("timestamp", ""),
            "chunks": int(fields.get("chunks", "0")),
            "error": fields.get("error", ""),
        })

    # Total count in stream
    try:
        total = r.xlen(INGEST_HISTORY_STREAM)
    except (CeridError, ValueError, OSError, RuntimeError):
        total = len(items)

    next_cursor = items[-1]["id"] if len(items) == limit else None

    return {
        "items": items,
        "total": total,
        "next_cursor": next_cursor,
    }


# ── Utility: push to ingest history stream ────────────────────────────────────


def record_ingest_event(
    filename: str,
    source_type: str,
    domain: str,
    status: str,
    chunks: int = 0,
    error: str = "",
) -> None:
    """Push an ingestion event to the persistent Redis stream.

    Called from services/ingestion.py after each ingest_content() or ingest_file().
    Stream entries auto-expire based on INGEST_HISTORY_RETENTION_DAYS.
    """
    try:
        from utils.time import utcnow_iso

        r = get_redis()
        r.xadd(
            INGEST_HISTORY_STREAM,
            {
                "filename": filename,
                "source_type": source_type,
                "domain": domain,
                "status": status,
                "chunks": str(chunks),
                "error": error[:500] if error else "",
                "timestamp": utcnow_iso(),
            },
        )
        # Trim old entries: keep last N days worth or max 10k entries
        retention_ms = INGEST_HISTORY_RETENTION_DAYS * 86400 * 1000
        r.xtrim(INGEST_HISTORY_STREAM, minid=f"{int(time.time() * 1000) - retention_ms}-0")
    except (CeridError, ValueError, OSError, RuntimeError) as e:
        logger.debug("Failed to record ingest event: %s", e)
