# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Thread-safe LRU cache for embedding vectors.

Lookup is keyed on ``(namespace, sha256(text))`` so the consumer can isolate
vector spaces — e.g. when Quenchforge serves nomic-embed but the local
ONNX fallback would serve Snowflake Arctic-Embed, the two model families
must not share entries. The namespace string is opaque; the caller picks
it from whatever identity matches the producing model.

Targeted workload: LongMemEval haystacks reuse sessions across items
(~30% redundancy by the v0.95.9 trace). Cerid ingest also re-embeds
identical artifact text across rectify / dedupe paths. Both benefit
from a per-process cache without any cross-run persistence concerns —
the cache resets on process restart, which matches embedding-model
config changes (a new model wants fresh vectors anyway).

Why not Redis: this cache lives in front of a sync ChromaDB callback
that can run on background threads. A Redis round-trip per text would
defeat the cache's purpose for hot paths. Per-process is enough — the
hot consumers are long-lived (MCP server, eval runner) and a 50k-entry
bound covers the realistic redundancy window.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np

logger = logging.getLogger("ai-companion.embedding_cache")


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EmbeddingCache:
    """Bounded LRU cache mapping ``(namespace, text)`` to a vector.

    Stored vectors are forced through ``np.ascontiguousarray(dtype=float32)``
    on insert so the cache owns a fresh, contiguous buffer that callers
    can read without keeping a parent batch alive.
    """

    def __init__(self, max_size: int) -> None:
        self._max_size = max_size
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple[str, str], np.ndarray] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, namespace: str, text: str) -> np.ndarray | None:
        if self._max_size <= 0:
            return None
        key = (namespace, _hash_text(text))
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return entry

    def put(self, namespace: str, text: str, vector: np.ndarray) -> None:
        if self._max_size <= 0:
            return
        stored = np.ascontiguousarray(np.asarray(vector, dtype=np.float32))
        key = (namespace, _hash_text(text))
        with self._lock:
            self._entries[key] = stored
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_size:
                self._entries.popitem(last=False)

    def stats(self) -> dict[str, float | int]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "size": len(self._entries),
                "max_size": self._max_size,
                "hit_rate": (self._hits / total) if total > 0 else 0.0,
            }

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0


class PersistentEmbeddingCache(EmbeddingCache):
    """LRU memory cache backed by an SQLite disk tier.

    Solves the cross-process / cross-run miss problem the in-memory LRU
    has: re-running an ablation against the same dataset re-embeds the
    full ~22k chunks because the singleton cache resets with the
    process. The disk tier survives that, so subsequent runs hit it
    instead of the daemon.

    Cache key on disk is ``(namespace, sha256(text))`` — exactly the
    in-memory key. The namespace string encodes the active embed
    backend (e.g. ``qf:nomic-embed-text-v1.5``), so different backends
    coexist in the same SQLite file without overwriting each other.
    Switching backends just produces a different namespace and a
    different row.

    Failure modes are non-fatal: a missing parent directory, a locked
    SQLite file, or any sqlite3 exception logs a warning via
    ``log_swallowed_error`` and falls back to memory-only operation.
    The disk tier is best-effort cache; correctness never depends on it.

    Concurrency: SQLite handles process-level locking; the
    ``_db_lock`` here serializes calls from threads within ONE process so
    the embedding_fn's sync-bridge executor pool doesn't fight with
    itself. Cross-process writes are still safe via SQLite's WAL mode
    (set on first init).
    """

    SCHEMA_VERSION = 1

    def __init__(self, max_size: int, db_path: str | Path) -> None:
        super().__init__(max_size=max_size)
        self._db_path = Path(db_path)
        self._db_lock = threading.Lock()
        self._disk_hits = 0
        self._disk_misses = 0
        self._disk_enabled = self._init_db()

    def _init_db(self) -> bool:
        """Create the schema if missing. Returns False on failure so the
        cache silently degrades to memory-only without crashing the
        embedding path.
        """
        # Catch OSError (parent-not-a-directory, permission denied, etc.)
        # in addition to sqlite3.Error so a misconfigured CERID_EMBED_CACHE_PATH
        # never crashes the embedding hot path; the cache just stays
        # memory-only and logs the reason.
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(self._db_path), timeout=30.0) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS embeddings (
                        namespace TEXT NOT NULL,
                        text_hash TEXT NOT NULL,
                        vector BLOB NOT NULL,
                        dim INTEGER NOT NULL,
                        schema_version INTEGER NOT NULL DEFAULT 1,
                        PRIMARY KEY (namespace, text_hash)
                    )
                    """,
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_namespace "
                    "ON embeddings(namespace)",
                )
                conn.commit()
            return True
        except (sqlite3.Error, OSError) as exc:
            logger.warning(
                "PersistentEmbeddingCache disabled — disk init failed at %s: %s",
                self._db_path, exc,
            )
            return False

    def get(self, namespace: str, text: str) -> np.ndarray | None:
        # Memory tier first — fast path is unchanged.
        memvec = super().get(namespace, text)
        if memvec is not None:
            return memvec
        if not self._disk_enabled or self._max_size <= 0:
            return None
        # Disk fallthrough — promote to memory on hit so subsequent
        # calls don't repeat the sqlite round-trip.
        diskvec = self._disk_get(namespace, text)
        if diskvec is not None:
            with self._lock:
                self._disk_hits += 1
            # Promote to memory using the parent class's put — this
            # only touches the in-memory tier; we don't want a disk
            # write here (the row already exists on disk).
            super().put(namespace, text, diskvec)
            return diskvec
        with self._lock:
            self._disk_misses += 1
        return None

    def put(self, namespace: str, text: str, vector: np.ndarray) -> None:
        super().put(namespace, text, vector)
        if not self._disk_enabled or self._max_size <= 0:
            return
        self._disk_put(namespace, text, vector)

    def _disk_get(self, namespace: str, text: str) -> np.ndarray | None:
        text_hash = _hash_text(text)
        try:
            with self._db_lock, sqlite3.connect(
                str(self._db_path), timeout=30.0,
            ) as conn:
                row = conn.execute(
                    "SELECT vector FROM embeddings "
                    "WHERE namespace=? AND text_hash=?",
                    (namespace, text_hash),
                ).fetchone()
        except sqlite3.Error as exc:
            logger.warning(
                "PersistentEmbeddingCache disk get failed: %s — degrading "
                "to memory-only for the remainder of this process", exc,
            )
            with self._lock:
                self._disk_enabled = False
            return None
        if row is None:
            return None
        # .copy() so the returned buffer is owned (not a view on the
        # transient sqlite-allocated bytes object).
        return np.ascontiguousarray(
            np.frombuffer(row[0], dtype=np.float32).copy(),
        )

    def _disk_put(
        self, namespace: str, text: str, vector: np.ndarray,
    ) -> None:
        text_hash = _hash_text(text)
        stored = np.ascontiguousarray(np.asarray(vector, dtype=np.float32))
        dim = int(stored.shape[-1])
        try:
            with self._db_lock, sqlite3.connect(
                str(self._db_path), timeout=30.0,
            ) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO embeddings "
                    "(namespace, text_hash, vector, dim, schema_version) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        namespace, text_hash, stored.tobytes(),
                        dim, self.SCHEMA_VERSION,
                    ),
                )
                conn.commit()
        except sqlite3.Error as exc:
            logger.warning(
                "PersistentEmbeddingCache disk put failed: %s — degrading "
                "to memory-only for the remainder of this process", exc,
            )
            with self._lock:
                self._disk_enabled = False

    def stats(self) -> dict[str, float | int]:  # type: ignore[override]
        # Disk-tier counters live alongside the memory counters; the
        # stat schema is intentionally additive so /health rendering
        # picks up the new fields without a contract change.
        base: dict[str, float | int | str | bool] = dict(super().stats())
        with self._lock:
            base["disk_hits"] = self._disk_hits
            base["disk_misses"] = self._disk_misses
            base["disk_enabled"] = bool(self._disk_enabled)
            base["disk_path"] = str(self._db_path)
        return base  # type: ignore[return-value]


_singleton: EmbeddingCache | None = None
_singleton_lock = threading.Lock()


def get_embedding_cache() -> EmbeddingCache:
    """Return the process-wide cache singleton.

    Size is read from ``CERID_EMBED_CACHE_SIZE`` once at first call;
    set to ``0`` to disable the cache entirely (get / put become no-ops).

    Disk tier: if ``CERID_EMBED_CACHE_PATH`` is set to a writable path,
    the singleton is a :class:`PersistentEmbeddingCache` that survives
    across processes. Default empty = memory-only (the pre-v0.96.1
    behaviour). Cross-run ablation work benefits the most — the first
    run pays the embed cost, subsequent ones read from disk.
    """
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is not None:
            return _singleton
        raw = os.environ.get("CERID_EMBED_CACHE_SIZE", "50000")
        try:
            max_size = int(raw)
        except ValueError:
            logger.warning(
                "Invalid CERID_EMBED_CACHE_SIZE=%r; defaulting to 50000", raw,
            )
            max_size = 50000
        disk_path = os.environ.get("CERID_EMBED_CACHE_PATH", "").strip()
        if disk_path:
            _singleton = PersistentEmbeddingCache(
                max_size=max_size, db_path=disk_path,
            )
            logger.info(
                "embedding cache: memory+disk tier at %s (max_size=%d)",
                disk_path, max_size,
            )
        else:
            _singleton = EmbeddingCache(max_size=max_size)
        return _singleton


def _reset_singleton_for_testing() -> None:
    """Drop the singleton so the next call re-reads the env var. Tests only."""
    global _singleton
    with _singleton_lock:
        _singleton = None
