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
from utils.encryption import decrypt_field

# Dead-letter: an orphan chunk's content + metadata are persisted here BEFORE it
# is purged from Chroma, so an unrecoverable orphan (Neo4j permanently down
# through the retry budget) is operator-recoverable instead of silently lost.
_DEADLETTER_KEY = "cerid:ingest_deadletter"
_DEADLETTER_MAX = 1000  # bound the list; oldest trimmed

logger = logging.getLogger("ai-companion.ingest_recovery")

# How many Neo4j commit attempts before giving up and purging.
_MAX_RECOVERY_ATTEMPTS = 2

# AF-018: bound the pending-chunk fetch per collection so a large backlog of
# stale pending rows cannot load unboundedly into memory. Chunks beyond the cap
# are recovered on a later scan (recovery is idempotent), and the cap being hit
# is logged (never a silent truncation).
_SCAN_MAX_CHUNKS_PER_COLLECTION = 5000


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
            limit=_SCAN_MAX_CHUNKS_PER_COLLECTION,
        )
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.services.ingest_recovery.fetch_pending",
            e,
            context={"collection": collection_name},
        )
        return []

    ids = result.get("ids") or []
    if len(ids) >= _SCAN_MAX_CHUNKS_PER_COLLECTION:
        logger.warning(
            "ingest_recovery.scan_capped collection=%s at %d pending chunks — "
            "remaining orphans recover on the next scan",
            collection_name, _SCAN_MAX_CHUNKS_PER_COLLECTION,
        )
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


def group_orphans_by_artifact(
    orphans: list[OrphanRecord],
) -> dict[str, list[OrphanRecord]]:
    """Group orphan chunk records by ``artifact_id`` so recovery rebuilds each
    artifact's node ONCE with the real ``chunk_count`` (AF-003). Records that
    share an ``artifact_id`` also share a domain + collection. Orphans with an
    empty artifact_id each recover as their own singleton group."""
    groups: dict[str, list[OrphanRecord]] = {}
    for orphan in orphans:
        groups.setdefault(orphan.artifact_id, []).append(orphan)
    return groups


async def recover_artifact(orphans: list[OrphanRecord]) -> RecoveryAction:
    """Roll-forward ALL orphaned pending chunks of ONE artifact in a single
    Neo4j write (AF-003).

    Every record must share the same ``artifact_id`` (hence domain +
    collection). Rebuilding the node with the real ``chunk_count = len(orphans)``
    and the full ``chunk_ids`` list — rather than once-per-chunk with
    ``chunk_count=1`` — is why multi-chunk artifacts no longer collapse to a
    single visible chunk after recovery.

    Strategy mirrors the per-chunk path: bump the recovery-attempt counter on
    every chunk, attempt one ``graph.create_artifact``, flip every chunk to
    ``committed`` on success, and on retry-exhaustion dead-letter + purge every
    chunk. Idempotent — a re-run MERGEs the same node (``ON MATCH SET`` repairs
    ``chunk_count``/``chunk_ids``).
    """
    if not orphans:
        return RecoveryAction.DEFERRED

    chroma = get_chroma()
    driver = get_neo4j()
    lead = orphans[0]
    chunk_ids = [o.chunk_id for o in orphans]
    new_attempt_count = max(o.retry_count for o in orphans) + 1

    try:
        collection = await asyncio.to_thread(
            chroma.get_collection, name=lead.collection_name
        )
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.services.ingest_recovery.get_collection_for_recover",
            e,
            context={"artifact_id": lead.artifact_id},
        )
        return RecoveryAction.DEFERRED

    # --- 1. Bump attempt counter on every chunk --------------------------
    try:
        await asyncio.to_thread(
            collection.update,
            ids=chunk_ids,
            metadatas=[{"cerid_recovery_attempts": new_attempt_count} for _ in chunk_ids],
        )
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.services.ingest_recovery.update_attempt_count",
            e,
            context={"artifact_id": lead.artifact_id},
        )
        # Non-fatal: proceed with recovery attempt anyway.

    # --- 2. One Neo4j commit for the whole artifact ----------------------
    meta = lead.metadata  # artifact-level fields are identical across chunks
    neo4j_ok = False
    try:
        await asyncio.to_thread(
            graph.create_artifact,
            driver,
            artifact_id=lead.artifact_id,
            filename=meta.get("filename", "recovered_artifact"),
            domain=lead.domain,
            keywords_json=meta.get("keywords_json", "[]"),
            # meta is the raw Chroma chunk metadata — "summary" may carry the
            # enc:v1: Chroma-only ciphertext (see CHROMA_ENCRYPTED_FIELDS).
            # decrypt_field no-ops on plaintext, so this is safe either way,
            # and it keeps Neo4j's summary property queryable cleartext.
            summary=decrypt_field(meta.get("summary", lead.document[:200])),
            chunk_count=len(chunk_ids),  # AF-003: the real N, not 1
            chunk_ids_json=json.dumps(chunk_ids),
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
        # committed this artifact — treat as success so we can flip the chunks.
        if "constraint" in err_msg and "content_hash" in err_msg:
            logger.info(
                "ingest_recovery.constraint_collision artifact=%s — treating as committed",
                lead.artifact_id,
            )
            neo4j_ok = True
        else:
            logger.warning(
                "ingest_recovery.neo4j_failed artifact=%s chunks=%d attempt=%d/%d: %s",
                lead.artifact_id,
                len(chunk_ids),
                new_attempt_count,
                _MAX_RECOVERY_ATTEMPTS,
                e,
            )

    # --- 3. On success: flip every chunk to committed --------------------
    if neo4j_ok:
        try:
            await asyncio.to_thread(
                collection.update,
                ids=chunk_ids,
                metadatas=[{"cerid_state": "committed"} for _ in chunk_ids],
            )
        except Exception as e:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "app.services.ingest_recovery.flip_committed",
                e,
                context={"artifact_id": lead.artifact_id},
            )
        logger.info(
            "ingest_recovery.committed artifact=%s chunks=%d",
            lead.artifact_id, len(chunk_ids),
        )
        return RecoveryAction.COMMITTED

    # --- 4. On failure: deferred or purge --------------------------------
    if new_attempt_count < _MAX_RECOVERY_ATTEMPTS:
        return RecoveryAction.DEFERRED

    # Budget exhausted — dead-letter each chunk's content, escalate once per
    # artifact, then purge. Dead-lettering BEFORE the delete means every chunk's
    # text + metadata survive even when Neo4j is permanently down (no silent loss).
    for orphan in orphans:
        await _deadletter_orphan(orphan, new_attempt_count)
    _escalate_orphan(lead, new_attempt_count)
    try:
        await asyncio.to_thread(collection.delete, ids=chunk_ids)
        logger.warning(
            "ingest_recovery.purged artifact=%s chunks=%d (exhausted %d retries)",
            lead.artifact_id,
            len(chunk_ids),
            new_attempt_count,
        )
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "app.services.ingest_recovery.purge",
            e,
            context={"artifact_id": lead.artifact_id},
        )
    return RecoveryAction.PURGED


async def recover_orphan(orphan: OrphanRecord) -> RecoveryAction:
    """Compat shim — recover a single orphaned chunk as an artifact group of one.

    Retained for callers/tests that operate per-chunk; the artifact-granular
    :func:`recover_artifact` is the primary path (the recovery job groups
    orphans by ``artifact_id`` before calling it, closing AF-003)."""
    return await recover_artifact([orphan])


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
