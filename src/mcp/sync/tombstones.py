# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tombstone support for propagating artifact deletions across machines."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

import config
from sync._helpers import (
    NEO4J_SUBDIR,
    TOMBSTONES_JSONL,
    _default_sync_dir,
    _ensure_dir,
    _iter_jsonl,
)

logger = logging.getLogger("ai-companion.sync")


def record_tombstone(
    artifact_id: str,
    chunk_ids: list[str],
    domain: str = "",
    filename: str = "",
) -> None:
    """Append a deletion record to the local tombstone log."""
    entry = {
        "artifact_id": artifact_id,
        "chunk_ids": chunk_ids,
        "domain": domain,
        "filename": filename,
        "deleted_at": datetime.now(UTC).isoformat(),
        "machine_id": config.MACHINE_ID,
    }
    log_path = config.TOMBSTONE_LOG_PATH
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")
    logger.info("Tombstone recorded for artifact %s", artifact_id[:8])


def export_tombstones(sync_dir: str | None = None) -> dict[str, Any]:
    """Merge local tombstones into sync_dir, dedup, and purge expired entries."""
    sync_dir = sync_dir or _default_sync_dir()
    out_dir = str(Path(sync_dir) / NEO4J_SUBDIR)
    _ensure_dir(out_dir)
    out_path = os.path.join(out_dir, TOMBSTONES_JSONL)

    cutoff = datetime.now(UTC) - timedelta(days=config.TOMBSTONE_TTL_DAYS)

    # Load existing sync tombstones (dedup by artifact_id, drop expired)
    existing: dict[str, dict] = {}
    loaded_from_sync = 0
    for row in _iter_jsonl(out_path):
        aid = row.get("artifact_id")
        if not aid:
            continue
        loaded_from_sync += 1
        deleted_at = _parse_ts(row.get("deleted_at", ""))
        if deleted_at and deleted_at > cutoff:
            existing[aid] = row

    # Count expired entries purged from sync file
    purged = loaded_from_sync - len(existing)

    # Merge local tombstones
    added = 0
    for row in _iter_jsonl(config.TOMBSTONE_LOG_PATH):
        aid = row.get("artifact_id")
        if not aid or aid in existing:
            continue
        deleted_at = _parse_ts(row.get("deleted_at", ""))
        if deleted_at and deleted_at > cutoff:
            existing[aid] = row
            added += 1

    # Write merged file (also acts as purge of expired entries)
    with open(out_path, "w", encoding="utf-8") as fh:
        for row in existing.values():
            fh.write(json.dumps(row, default=str) + "\n")

    logger.info(
        "Tombstone export: %d total, %d new → %s", len(existing), added, out_path
    )
    return {
        "tombstones_exported": len(existing),
        "new_entries": added,
        "purged_expired": purged,
        "output_path": out_path,
    }


def apply_tombstones(
    driver,
    chroma_url: str,
    sync_dir: str | None = None,
) -> dict[str, Any]:
    """Delete artifacts flagged by remote tombstones. Skips own-machine tombstones."""
    sync_dir = sync_dir or _default_sync_dir()
    tombstone_path = os.path.join(sync_dir, NEO4J_SUBDIR, TOMBSTONES_JSONL)

    deleted = 0
    skipped_own = 0
    skipped_absent = 0
    errors = 0

    for row in _iter_jsonl(tombstone_path):
        artifact_id = row.get("artifact_id")
        if not artifact_id:
            continue

        # Skip deletions that originated on this machine
        if row.get("machine_id") == config.MACHINE_ID:
            skipped_own += 1
            continue

        chunk_ids: list[str] = row.get("chunk_ids", [])
        domain = row.get("domain", "")

        # Delete from Neo4j
        try:
            with driver.session() as session:
                result = session.run(
                    "MATCH (a:Artifact {id: $id}) DETACH DELETE a RETURN count(a) AS n",
                    id=artifact_id,
                )
                record = result.single()
                if record and record["n"] == 0:
                    # Already deleted or never existed locally
                    skipped_absent += 1
                    continue
        except Exception as exc:
            logger.warning("Tombstone Neo4j delete failed for %s: %s", artifact_id[:8], exc)
            errors += 1
            continue

        # Delete chunks from ChromaDB
        if chunk_ids and domain:
            _delete_chroma_chunks(chroma_url, domain, chunk_ids)
        elif chunk_ids:
            # Try all configured domains
            for d in config.DOMAINS:
                _delete_chroma_chunks(chroma_url, d, chunk_ids)

        deleted += 1
        logger.info("Tombstone applied: deleted artifact %s", artifact_id[:8])

    return {
        "deleted": deleted,
        "skipped_own_machine": skipped_own,
        "skipped_absent": skipped_absent,
        "errors": errors,
    }


def purge_expired(sync_dir: str | None = None) -> dict[str, Any]:
    """Remove tombstone entries older than TTL from both local log and sync dir."""
    cutoff = datetime.now(UTC) - timedelta(days=config.TOMBSTONE_TTL_DAYS)
    purged = 0

    # Purge local log
    purged += _purge_file(config.TOMBSTONE_LOG_PATH, cutoff)

    # Purge sync dir
    if sync_dir:
        sync_path = os.path.join(sync_dir, NEO4J_SUBDIR, TOMBSTONES_JSONL)
        purged += _purge_file(sync_path, cutoff)

    return {"purged": purged}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_ts(ts_str: str) -> datetime | None:
    """Parse an ISO-8601 timestamp, returning None on failure."""
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return None


def _delete_chroma_chunks(chroma_url: str, domain: str, chunk_ids: list[str]) -> None:
    """Best-effort delete chunk IDs from a ChromaDB collection."""
    coll_name = config.collection_name(domain)
    try:
        resp = httpx.get(
            f"{chroma_url}/api/v1/collections/{coll_name}",
            timeout=10.0,
        )
        if resp.status_code != 200:
            return
        coll_id = resp.json().get("id", coll_name)
        del_resp = httpx.post(
            f"{chroma_url}/api/v1/collections/{coll_id}/delete",
            json={"ids": chunk_ids},
            timeout=30.0,
        )
        del_resp.raise_for_status()
    except Exception as exc:
        logger.warning(
            "Tombstone ChromaDB delete failed for %s/%s: %s",
            domain, chunk_ids[0][:8] if chunk_ids else "?", exc,
        )


def _purge_file(filepath: str, cutoff: datetime) -> int:
    """Rewrite a tombstone JSONL file, removing entries older than cutoff."""
    if not os.path.exists(filepath):
        return 0

    kept: list[str] = []
    purged = 0
    for row in _iter_jsonl(filepath):
        deleted_at = _parse_ts(row.get("deleted_at", ""))
        if deleted_at and deleted_at > cutoff:
            kept.append(json.dumps(row, default=str))
        else:
            purged += 1

    if purged:
        with open(filepath, "w", encoding="utf-8") as fh:
            for line in kept:
                fh.write(line + "\n")

    return purged
