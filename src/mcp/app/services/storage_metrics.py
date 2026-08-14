# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Shared storage-usage helpers (AF-042).

Two consumers need the identical answer to "how full is the corpus":

1. ``GET /system/storage`` (``app/routers/system_monitor.py``) — read-only
   report for the UI.
2. Ingest backpressure (``app/services/ingestion.py``) — rejects new ingest
   once usage crosses ``STORAGE_CRITICAL_PCT``.

Both call :func:`get_storage_report`, which does the per-store aggregation
(ChromaDB/Neo4j/Redis/BM25 disk + memory size) ONCE and caches it in Redis
for ``_STORAGE_CACHE_TTL`` seconds — so wiring the backpressure check into
the ingest hot path does not add a directory walk + three DB round-trips to
every single ingest call.

The ChromaDB/Neo4j/Redis connection getters are accepted as parameters
(defaulting to the canonical ``app.deps`` singletons) rather than imported
and called directly. ``ingest_content`` already depends on — and every
existing ingest test already mocks — ``app.services.ingestion.get_chroma``
/ ``get_neo4j`` / ``get_redis``; passing those same references through
means the backpressure check rides on the SAME mocks instead of opening a
second, unmocked path to the real connection singletons (which retry with
backoff on failure) from every ingest call in the test suite.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable

from app.deps import get_chroma, get_neo4j, get_redis
from config.settings import (
    BM25_DATA_DIR,
    STORAGE_CRITICAL_PCT,
    STORAGE_LIMIT_MB,
    STORAGE_WARN_PCT,
)
from errors import CeridError

logger = logging.getLogger("ai-companion")

# Redis cache key + TTL for storage metrics
_STORAGE_CACHE_KEY = "cerid:system:storage_metrics"
_STORAGE_CACHE_TTL = 60  # seconds


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


def _chromadb_metrics(get_chroma_fn: Callable[[], Any]) -> dict:
    """ChromaDB: collection count, total chunks, disk size."""
    try:
        client = get_chroma_fn()
        collections = client.list_collections()
        total_chunks = 0
        for coll in collections:
            try:
                total_chunks += coll.count()
            except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
                pass  # Collection count: skip unavailable collections
        # Disk size: ChromaDB persist directory inside the container
        chroma_dir = os.getenv("CHROMA_PERSIST_DIR", "/chroma/chroma")
        disk_mb = _dir_size_mb(chroma_dir)
        return {
            "disk_mb": disk_mb,
            "collections": len(collections),
            "chunks": total_chunks,
        }
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning("ChromaDB metrics unavailable: %s", e)
        return {"disk_mb": 0, "collections": 0, "chunks": 0, "error": str(e)}


def _neo4j_metrics(get_neo4j_fn: Callable[[], Any]) -> dict:
    """Neo4j: node count, relationship count."""
    driver = get_neo4j_fn()
    if driver is None:
        return {"disk_mb": 0, "nodes": 0, "relationships": 0, "status": "disabled"}
    try:
        with driver.session() as session:
            nodes = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rels = session.run("MATCH ()-[r]-() RETURN count(r) AS c").single()["c"]
        return {"disk_mb": 0, "nodes": nodes, "relationships": rels}
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.warning("Neo4j metrics unavailable: %s", e)
        return {"disk_mb": 0, "nodes": 0, "relationships": 0, "error": str(e)}


def _redis_metrics(get_redis_fn: Callable[[], Any]) -> dict:
    """Redis: memory usage, peak memory, key count."""
    try:
        r = get_redis_fn()
        info = r.info("memory")
        used = round(info.get("used_memory", 0) / (1024 * 1024), 2)
        peak = round(info.get("used_memory_peak", 0) / (1024 * 1024), 2)
        keys = r.dbsize()
        return {"memory_mb": used, "peak_mb": peak, "keys": keys}
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
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


def classify_storage_status(usage_pct: float, warn_pct: int, critical_pct: int) -> str:
    """Classify a usage percentage against the WARN/CRITICAL thresholds.

    Boundaries are inclusive on the upper band: ``usage_pct == warn_pct`` is
    already "warning", ``usage_pct == critical_pct`` is already "critical" —
    matches the ``>=`` comparison the read-only report has always used.
    """
    if usage_pct >= critical_pct:
        return "critical"
    if usage_pct >= warn_pct:
        return "warning"
    return "healthy"


def get_storage_report(
    *,
    use_cache: bool = True,
    get_chroma_fn: Callable[[], Any] | None = None,
    get_neo4j_fn: Callable[[], Any] | None = None,
    get_redis_fn: Callable[[], Any] | None = None,
) -> dict:
    """Return storage usage across all data stores, cached for 60 seconds.

    The single canonical computation of "how full is the corpus" — both the
    read-only ``GET /system/storage`` endpoint and the ingest backpressure
    check in ``app/services/ingestion.py`` call this instead of duplicating
    the per-store aggregation or the usage-pct/threshold math.

    ``get_*_fn`` default to the canonical ``app.deps`` connection getters;
    callers that already hold their own (test-mockable) references — like
    ``app.services.ingestion``, which every ingest test already patches —
    should pass them through instead of letting this module open a second,
    independent connection path.
    """
    get_chroma_fn = get_chroma_fn or get_chroma
    get_neo4j_fn = get_neo4j_fn or get_neo4j
    get_redis_fn = get_redis_fn or get_redis

    if use_cache:
        try:
            r = get_redis_fn()
            cached = r.get(_STORAGE_CACHE_KEY)
            if cached:
                return json.loads(cached)
        except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
            pass  # Storage cache: compute fresh on miss

    chromadb = _chromadb_metrics(get_chroma_fn)
    neo4j = _neo4j_metrics(get_neo4j_fn)
    redis_m = _redis_metrics(get_redis_fn)
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
        "status": classify_storage_status(pct, STORAGE_WARN_PCT, STORAGE_CRITICAL_PCT),
        "timestamp": time.time(),
    }

    # Cache in Redis for 60 seconds
    try:
        r = get_redis_fn()
        r.setex(_STORAGE_CACHE_KEY, _STORAGE_CACHE_TTL, json.dumps(result))
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        pass  # Storage cache: best-effort write

    return result
