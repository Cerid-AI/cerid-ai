# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Watched folders CRUD — manage auto-ingesting directories with per-folder settings.

Each folder tracks: path, domain override, exclude patterns, search_enabled toggle,
and scan statistics. Storage: Redis hash per folder (survives restarts).

Dependencies: config/settings.py, services/folder_scanner.py
Error types: CeridError (from errors.py)
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, Field

from errors import CeridError

# Allowed root directories for watched folders (prevents path traversal)
_ALLOWED_ROOTS = [
    pathlib.Path(os.getenv("ARCHIVE_PATH", "/archive")).resolve(),
    pathlib.Path(os.path.expanduser("~/cerid-archive")).resolve(),
]

router = APIRouter(prefix="/watched-folders", tags=["watched-folders"])
logger = logging.getLogger("ai-companion.watched_folders")

_REDIS_PREFIX = "cerid:watched_folders"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class WatchedFolderCreate(BaseModel):
    path: str = Field(..., description="Absolute path to the directory")
    label: str = Field("", description="User-friendly display name")
    domain_override: str | None = Field(None, description="Force domain classification for all files")
    exclude_patterns: list[str] = Field(default_factory=lambda: [".git", "node_modules", "__pycache__", ".DS_Store"])
    search_enabled: bool = Field(True, description="Include this folder's chunks in RAG queries")


class WatchedFolderUpdate(BaseModel):
    label: str | None = None
    enabled: bool | None = None
    domain_override: str | None = None
    exclude_patterns: list[str] | None = None
    search_enabled: bool | None = None


class WatchedFolderResponse(BaseModel):
    id: str
    path: str
    label: str
    enabled: bool
    domain_override: str | None
    exclude_patterns: list[str]
    search_enabled: bool
    last_scanned_at: str | None
    stats: dict
    created_at: str


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------

def _get_redis():
    """Get Redis client from deps module."""
    from deps import get_redis
    return get_redis()


def _folder_key(folder_id: str) -> str:
    return f"{_REDIS_PREFIX}:{folder_id}"


def _load_folder(redis, folder_id: str) -> dict | None:
    """Load a single folder config from Redis."""
    raw = redis.get(_folder_key(folder_id))
    if not raw:
        return None
    return json.loads(raw)


def _save_folder(redis, folder_id: str, data: dict) -> None:
    """Persist a folder config to Redis."""
    redis.set(_folder_key(folder_id), json.dumps(data))


def _list_folder_ids(redis) -> list[str]:
    """Get all watched folder IDs from the index set."""
    return [m.decode() if isinstance(m, bytes) else m for m in redis.smembers(f"{_REDIS_PREFIX}:index")]


def _add_to_index(redis, folder_id: str) -> None:
    redis.sadd(f"{_REDIS_PREFIX}:index", folder_id)


def _remove_from_index(redis, folder_id: str) -> None:
    redis.srem(f"{_REDIS_PREFIX}:index", folder_id)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("")
async def create_watched_folder(body: WatchedFolderCreate):
    """Add a new watched folder."""
    # Validate path exists and is within allowed roots
    resolved = pathlib.Path(body.path).resolve()
    if not resolved.is_dir():
        raise CeridError(f"Directory not found: {body.path}", status_code=400)
    if not any(resolved == root or root in resolved.parents for root in _ALLOWED_ROOTS):
        raise CeridError(
            f"Path must be within an allowed directory ({', '.join(str(r) for r in _ALLOWED_ROOTS)})",
            status_code=400,
        )

    redis = _get_redis()
    folder_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()

    data = {
        "id": folder_id,
        "path": body.path,
        "label": body.label or os.path.basename(body.path),
        "enabled": True,
        "domain_override": body.domain_override,
        "exclude_patterns": body.exclude_patterns,
        "search_enabled": body.search_enabled,
        "last_scanned_at": None,
        "stats": {"ingested": 0, "skipped": 0, "errored": 0},
        "created_at": now,
    }

    _save_folder(redis, folder_id, data)
    _add_to_index(redis, folder_id)

    logger.info("Created watched folder %s: %s", folder_id, body.path)
    return data


@router.get("")
async def list_watched_folders():
    """List all watched folders with status."""
    redis = _get_redis()
    folder_ids = _list_folder_ids(redis)

    folders = []
    for fid in folder_ids:
        data = _load_folder(redis, fid)
        if data:
            folders.append(data)

    # Sort by created_at descending
    folders.sort(key=lambda f: f.get("created_at", ""), reverse=True)
    return {"folders": folders, "total": len(folders)}


@router.get("/{folder_id}")
async def get_watched_folder(folder_id: str):
    """Get a specific watched folder."""
    redis = _get_redis()
    data = _load_folder(redis, folder_id)
    if not data:
        raise CeridError(f"Watched folder not found: {folder_id}", status_code=404)
    return data


@router.patch("/{folder_id}")
async def update_watched_folder(folder_id: str, body: WatchedFolderUpdate):
    """Update a watched folder's settings."""
    redis = _get_redis()
    data = _load_folder(redis, folder_id)
    if not data:
        raise CeridError(f"Watched folder not found: {folder_id}", status_code=404)

    if body.label is not None:
        data["label"] = body.label
    if body.enabled is not None:
        data["enabled"] = body.enabled
    if body.domain_override is not None:
        data["domain_override"] = body.domain_override or None
    if body.exclude_patterns is not None:
        data["exclude_patterns"] = body.exclude_patterns
    if body.search_enabled is not None:
        data["search_enabled"] = body.search_enabled

    _save_folder(redis, folder_id, data)
    logger.info("Updated watched folder %s", folder_id)
    return data


@router.delete("/{folder_id}")
async def delete_watched_folder(folder_id: str):
    """Remove a watched folder (does not delete ingested content)."""
    redis = _get_redis()
    data = _load_folder(redis, folder_id)
    if not data:
        raise CeridError(f"Watched folder not found: {folder_id}", status_code=404)

    redis.delete(_folder_key(folder_id))
    _remove_from_index(redis, folder_id)

    logger.info("Deleted watched folder %s: %s", folder_id, data.get("path"))
    return {"status": "deleted", "id": folder_id, "path": data.get("path")}


@router.post("/{folder_id}/scan")
async def scan_watched_folder(folder_id: str, background_tasks: BackgroundTasks):
    """Trigger a scan on a specific watched folder."""
    redis = _get_redis()
    data = _load_folder(redis, folder_id)
    if not data:
        raise CeridError(f"Watched folder not found: {folder_id}", status_code=404)

    if not os.path.isdir(data["path"]):
        raise CeridError(f"Directory not accessible: {data['path']}", status_code=400)

    async def _run_scan():
        from services.folder_scanner import scan_folder

        ingested = 0
        skipped = 0
        errored = 0

        try:
            async for result in scan_folder(
                data["path"],
                exclude_patterns=set(data.get("exclude_patterns", [])),
            ):
                if result.status == "ingested":
                    ingested += 1
                elif result.status in ("duplicate", "low_quality", "skipped", "unsupported"):
                    skipped += 1
                elif result.status == "error":
                    errored += 1
        except Exception as exc:
            logger.error("Scan failed for folder %s: %s", folder_id, exc)
            errored += 1

        # Re-load to avoid overwriting concurrent PATCH updates
        current = _load_folder(redis, folder_id) or data
        current["stats"] = {"ingested": ingested, "skipped": skipped, "errored": errored}
        current["last_scanned_at"] = datetime.now(timezone.utc).isoformat()
        _save_folder(redis, folder_id, current)
        logger.info("Scan complete for folder %s: %d ingested, %d skipped, %d errors",
                     folder_id, ingested, skipped, errored)

    background_tasks.add_task(_run_scan)

    return {
        "status": "scan_started",
        "id": folder_id,
        "path": data["path"],
    }


@router.get("/{folder_id}/status")
async def get_folder_status(folder_id: str):
    """Get scan status for a specific folder."""
    redis = _get_redis()
    data = _load_folder(redis, folder_id)
    if not data:
        raise CeridError(f"Watched folder not found: {folder_id}", status_code=404)

    return {
        "id": folder_id,
        "path": data["path"],
        "enabled": data["enabled"],
        "last_scanned_at": data.get("last_scanned_at"),
        "stats": data.get("stats", {}),
    }
