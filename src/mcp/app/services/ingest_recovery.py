# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ingest recovery service — scan and heal orphaned pending Chroma chunks.

Phase O.1 of v0.92.  When the two-phase ingest boundary in
``app/services/ingestion.py`` stages a chunk as ``cerid_state="pending"``
and the subsequent Neo4j write fails, the chunk remains in pending state
indefinitely.  This service provides two functions:

``scan_orphans``
    Queries every configured ChromaDB collection for chunks whose
    ``cerid_state`` is ``"pending"`` and ``cerid_pending_at`` is older
    than ``max_age_seconds`` (default 60).  Returns a list of
    :class:`OrphanRecord` objects.

``recover_orphan``
    Attempts to roll-forward a single orphan by re-trying the Neo4j
    ``create_artifact`` call.  On success, flips the Chroma row to
    ``cerid_state="committed"``.  On failure after two retries, adds a
    Sentry breadcrumb (observability) and purges the Chroma row — the
    artifact will need to be re-ingested from source.

This module lives in ``app/services/`` and must never be imported by
``core/*`` (enforced by import-linter).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import config
from app.db import neo4j as graph
from app.deps import get_chroma, get_neo4j, get_redis
from core.utils.swallowed import log_swallowed_error

# Dead-letter: an orphan chunk's content + metadata are persisted here BEFORE it
# is purged from Chroma, so an unrecoverable orphan (Neo4j permanently down
# through the retry budget) is operator-recoverable instead of silently lost.
_DEADLETTER_KEY = "cerid:ingest_deadletter"
_DEADLETTER_MAX = 1000  # bound the list; oldest trimmed

logger = logging.getLogger("ai-companion.ingest_recovery")

# How many Neo4j commit attempts before giving up and purging.
_MAX_RECOVERY_ATTEMPTS = 2


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


class RecoveryAction(str, Enum):
    """Outcome of a single orphan recovery attempt."""

    COMMITTED = "committed"
    """Neo4j commit succeeded; Chroma chunk is now ``committed``."""

    PURGED = "purged"
    """Neo4j commit failed after max retries; chunk purged from Chroma."""

    DEFERRED = "deferred"
    """Recovery failed but retry budget not yet exhausted; try again next tick."""


@dataclass
class OrphanRecord:
    """A Chroma chunk that is stuck in ``pending`` state.

    Attributes
    ----------
    chunk_id
        Chroma chunk identifier (the row's primary key).
    artifact_id
        The ``artifact_id`` metadata field on the chunk.
    domain
        The domain the collection belongs to.
    collection_name
        Chroma collection name.
    idempotency_key
        SHA-256 key from ``cerid_idempotency_key`` metadata, if present.
    pending_at
        ISO-8601 string from ``cerid_pending_at`` metadata.
    document
        The chunk text (fetched so recovery can re-attempt the Neo4j write
        with the original summary slice).
    metadata
        Full metadata dict from Chroma (used to reconstruct the artifact).
    retry_count
        Number of recovery attempts already made (tracked in metadata as
        ``cerid_recovery_attempts``).
    """

    chunk_id: str
    artifact_id: str
    domain: str
    collection_name: str
    idempotency_key: str
    pending_at: str
    document: str
    metadata: dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_pending_at(value: str | None) -> datetime | None:
    """Parse ISO timestamp stored in ``cerid_pending_at``; return None on error."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _is_stale(pending_at: str | None, max_age_seconds: float) -> bool:
    """Return True if the pending_at timestamp is older than max_age_seconds."""
    dt = _parse_pending_at(pending_at)
    if dt is None:
        # Unparseable timestamp → treat as stale to allow recovery.
        return True
    age = (datetime.now(tz=timezone.utc) - dt).total_seconds()
    return age >= max_age_seconds


def _get_all_collections(chroma_client: Any) -> list[Any]:
    """Return all ChromaDB collection handles; log but don't raise on failure."""
    try:
        return list(chroma_client.list_collections())
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error("app.services.ingest_recovery.list_collections", e)
        return []


def _fetch_pending_chunks(
    collection: Any,
    collection_name: str,
    max_age_seconds: float,
) -> list[OrphanRecord]:
    """Synchronous helper — run in a thread by the async callers."""
    try:
        result = collection.get(
            where={"cerid_state": {"$eq": "pending"}},
            include=["documents", "metadatas"],
        )
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.services.ingest_recovery.fetch_pending",
            e,
            context={"collection": collection_name},
        )
        return []

    ids = result.get("ids") or []
    docs = result.get("documents") or []
    metas = result.get("metadatas") or []

    orphans: list[OrphanRecord] = []
    for i, chunk_id in enumerate(ids):
        meta = metas[i] if i < len(metas) else {}
        doc = docs[i] if i < len(docs) else ""
        pending_at = meta.get("cerid_pending_at")
        if not _is_stale(pending_at, max_age_seconds):
            continue
        artifact_id = meta.get("artifact_id", "")
        domain = meta.get("domain", "")
        idempotency_key = meta.get("cerid_idempotency_key", "")
        retry_count = int(meta.get("cerid_recovery_attempts", 0))
        orphans.append(
            OrphanRecord(
                chunk_id=chunk_id,
                artifact_id=artifact_id,
                domain=domain,
                collection_name=collection_name,
                idempotency_key=idempotency_key,
                pending_at=pending_at or "",
                document=doc,
                metadata=dict(meta),
                retry_count=retry_count,
            )
        )
    return orphans


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def scan_orphans(*, max_age_seconds: float = 60.0) -> list[OrphanRecord]:
    """Scan all Chroma collections for stale pending chunks.

    A chunk is an orphan when:
    - ``cerid_state == "pending"``
    - ``cerid_pending_at`` is older than ``max_age_seconds``

    Parameters
    ----------
    max_age_seconds
        Minimum age (in seconds) a pending chunk must have before it is
        considered an orphan.  Default 60 s matches the Phase O.1 spec.

    Returns
    -------
    list[OrphanRecord]
        One record per orphaned chunk, across all collections.
    """
    chroma = get_chroma()
    raw_collections = await asyncio.to_thread(_get_all_collections, chroma)

    all_orphans: list[OrphanRecord] = []
    for raw_coll in raw_collections:
        coll_name: str = getattr(raw_coll, "name", str(raw_coll))
        try:
            collection = await asyncio.to_thread(
                chroma.get_collection, name=coll_name
            )
        except Exception as e:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "app.services.ingest_recovery.get_collection",
                e,
                context={"collection": coll_name},
            )
            continue
        orphans = await asyncio.to_thread(
            _fetch_pending_chunks, collection, coll_name, max_age_seconds
        )
        all_orphans.extend(orphans)

    if all_orphans:
        logger.info(
            "ingest_recovery.scan found %d orphan(s) (max_age=%ss)",
            len(all_orphans),
            max_age_seconds,
        )
    return all_orphans


async def recover_orphan(orphan: OrphanRecord) -> RecoveryAction:
    """Attempt to roll-forward a single orphaned chunk.

    Strategy
    --------
    1. Increment ``cerid_recovery_attempts`` in Chroma metadata.
    2. Attempt ``graph.create_artifact`` with the artifact metadata
       reconstructed from the Chroma metadata.
    3. On Neo4j success → flip ``cerid_state="committed"`` → return
       ``RecoveryAction.COMMITTED``.
    4. On Neo4j failure:
       - If ``retry_count < _MAX_RECOVERY_ATTEMPTS`` (after increment) →
         return ``RecoveryAction.DEFERRED`` (try again next worker tick).
       - If retry budget exhausted → add a Sentry breadcrumb, purge the
         Chroma row → return ``RecoveryAction.PURGED``.

    Parameters
    ----------
    orphan
        An :class:`OrphanRecord` returned by :func:`scan_orphans`.

    Returns
    -------
    RecoveryAction
        The outcome of this recovery attempt.
    """
    chroma = get_chroma()
    driver = get_neo4j()

    # --- 1. Increment attempt counter in Chroma metadata -----------------
    new_attempt_count = orphan.retry_count + 1
    try:
        collection = await asyncio.to_thread(
            chroma.get_collection, name=orphan.collection_name
        )
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.services.ingest_recovery.get_collection_for_recover",
            e,
            context={"chunk_id": orphan.chunk_id},
        )
        return RecoveryAction.DEFERRED

    try:
        await asyncio.to_thread(
            collection.update,
            ids=[orphan.chunk_id],
            metadatas=[{"cerid_recovery_attempts": new_attempt_count}],
        )
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.services.ingest_recovery.update_attempt_count",
            e,
            context={"chunk_id": orphan.chunk_id},
        )
        # Non-fatal: proceed with recovery attempt anyway.

    # --- 2. Attempt Neo4j commit -----------------------------------------
    meta = orphan.metadata
    neo4j_ok = False
    try:
        await asyncio.to_thread(
            graph.create_artifact,
            driver,
            artifact_id=orphan.artifact_id,
            filename=meta.get("filename", "recovered_artifact"),
            domain=orphan.domain,
            keywords_json=meta.get("keywords_json", "[]"),
            summary=meta.get("summary", orphan.document[:200]),
            chunk_count=1,  # conservative: one chunk visible to recovery
            chunk_ids_json=f'["{orphan.chunk_id}"]',
            content_hash=meta.get("content_hash", ""),
            sub_category=meta.get("sub_category", getattr(config, "DEFAULT_SUB_CATEGORY", "")),
            tags_json=meta.get("tags_json", "[]"),
            quality_score=float(meta.get("quality_score", 0.5)),
            client_source=meta.get("client_source", ""),
        )
        neo4j_ok = True
    except Exception as e:
        log_swallowed_error('app.services.ingest_recovery', e)
        err_msg = str(e).lower()
        # A content_hash constraint violation means another path already
        # committed this artifact — treat as success so we can flip the chunk.
        if "constraint" in err_msg and "content_hash" in err_msg:
            logger.info(
                "ingest_recovery.constraint_collision artifact=%s — treating as committed",
                orphan.artifact_id,
            )
            neo4j_ok = True
        else:
            logger.warning(
                "ingest_recovery.neo4j_failed chunk=%s attempt=%d/%d: %s",
                orphan.chunk_id,
                new_attempt_count,
                _MAX_RECOVERY_ATTEMPTS,
                e,
            )

    # --- 3. On success: flip chunk to committed ---------------------------
    if neo4j_ok:
        try:
            await asyncio.to_thread(
                collection.update,
                ids=[orphan.chunk_id],
                metadatas=[{"cerid_state": "committed"}],
            )
        except Exception as e:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "app.services.ingest_recovery.flip_committed",
                e,
                context={"chunk_id": orphan.chunk_id},
            )
        logger.info("ingest_recovery.committed chunk=%s", orphan.chunk_id)
        return RecoveryAction.COMMITTED

    # --- 4. On failure: deferred or purge --------------------------------
    if new_attempt_count < _MAX_RECOVERY_ATTEMPTS:
        return RecoveryAction.DEFERRED

    # Budget exhausted — dead-letter the content, raise a Sentry breadcrumb,
    # then purge. Dead-lettering BEFORE the delete means the chunk's text +
    # metadata survive even when Neo4j is permanently down (no silent data loss).
    await _deadletter_orphan(orphan, new_attempt_count)
    _escalate_orphan(orphan, new_attempt_count)
    try:
        await asyncio.to_thread(collection.delete, ids=[orphan.chunk_id])
        logger.warning(
            "ingest_recovery.purged chunk=%s artifact=%s (exhausted %d retries)",
            orphan.chunk_id,
            orphan.artifact_id,
            new_attempt_count,
        )
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.services.ingest_recovery.purge",
            e,
            context={"chunk_id": orphan.chunk_id},
        )
    return RecoveryAction.PURGED


async def _deadletter_orphan(orphan: OrphanRecord, attempt_count: int) -> None:
    """Persist an unrecoverable orphan's content + metadata to Redis before it
    is purged from Chroma. Best-effort: a Redis failure is logged (the Sentry
    breadcrumb still records identifiers) but never blocks the purge.
    """
    rc = get_redis()
    if rc is None:
        logger.warning(
            "ingest_recovery.deadletter_skipped chunk=%s (redis unavailable)",
            orphan.chunk_id,
        )
        return
    record = json.dumps({
        "chunk_id": orphan.chunk_id,
        "artifact_id": orphan.artifact_id,
        "domain": orphan.domain,
        "collection_name": orphan.collection_name,
        "idempotency_key": orphan.idempotency_key,
        "pending_at": orphan.pending_at,
        "attempt_count": attempt_count,
        "document": orphan.document,
        "metadata": orphan.metadata,
        "deadlettered_at": datetime.now(timezone.utc).isoformat(),
    })
    try:
        await asyncio.to_thread(rc.rpush, _DEADLETTER_KEY, record)
        await asyncio.to_thread(rc.ltrim, _DEADLETTER_KEY, -_DEADLETTER_MAX, -1)
    except Exception as e:  # noqa: BLE001 — best-effort; purge proceeds regardless
        log_swallowed_error(
            "app.services.ingest_recovery.deadletter",
            e,
            context={"chunk_id": orphan.chunk_id},
        )


def _escalate_orphan(orphan: OrphanRecord, attempt_count: int) -> None:
    """Add a Sentry breadcrumb for an unrecoverable orphan chunk."""
    try:
        import sentry_sdk  # type: ignore[import-not-found]
        sentry_sdk.add_breadcrumb(
            category="ingest_recovery",
            message=(
                f"Orphan chunk purged after {attempt_count} failed Neo4j "
                f"recovery attempts — artifact_id={orphan.artifact_id}, "
                f"chunk_id={orphan.chunk_id}, domain={orphan.domain}"
            ),
            level="warning",
            data={
                "chunk_id": orphan.chunk_id,
                "artifact_id": orphan.artifact_id,
                "domain": orphan.domain,
                "idempotency_key": orphan.idempotency_key,
                "pending_at": orphan.pending_at,
                "attempt_count": attempt_count,
            },
        )
    except Exception:  # noqa: BLE001 — Sentry is optional
        logger.warning(
            "ingest_recovery.sentry_breadcrumb_failed chunk=%s", orphan.chunk_id
        )
