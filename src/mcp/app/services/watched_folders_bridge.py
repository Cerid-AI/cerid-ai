# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Facade projecting the legacy watched-folders Redis store through the
canonical /sources API as the `folder` kind. Read-time projection +
write-through delegation; the watched-folders scanner stays the ingest
engine of record (folder is NOT in scheduler._POLLABLE_KINDS)."""
from __future__ import annotations

from typing import Any, Literal, cast

_PREFIX = "folder:"


# ---------------------------------------------------------------------------
# Write-through helpers (A4)
# ---------------------------------------------------------------------------


async def create_folder_source(
    redis: Any,  # noqa: ARG001 — folder handler fetches its own redis
    display_name: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Create a watched folder via the router's validated handler and return
    the projected SourceRecord dict. Path validation + _ALLOWED_ROOTS is
    enforced by ``create_watched_folder``; redis is fetched inside that
    handler so the ``redis`` parameter here is accepted but unused.

    Raises ``ValueError`` (→ 422 at the /sources router) when ``config.path``
    is missing/blank or ``import_mode`` is invalid, so a bad wizard payload
    surfaces as a friendly validation error instead of a 502."""
    from app.routers.watched_folders import (
        _ALLOWED_ROOTS,
        WatchedFolderCreate,
        create_watched_folder,
    )

    path = str(config.get("path") or "").strip()
    if not path:
        roots = ", ".join(str(r) for r in _ALLOWED_ROOTS)
        raise ValueError(
            "Folder sources need config.path — an absolute directory path "
            f"inside the Cerid container (allowed roots: {roots})."
        )

    raw_mode = str(config.get("import_mode") or "watch")
    if raw_mode not in ("watch", "once"):
        raise ValueError('import_mode must be "watch" or "once"')
    import_mode = cast(Literal["watch", "once"], raw_mode)

    detail = await create_watched_folder(
        WatchedFolderCreate(
            path=path,
            label=display_name,
            domain_override=config.get("domain_override"),
            exclude_patterns=config.get("exclude_patterns")
            or [".git", "node_modules", "__pycache__", ".DS_Store"],
            search_enabled=config.get("search_enabled", True),
            is_vault=bool(config.get("is_vault")),
            vault_config=config.get("vault_config"),
            import_mode=import_mode,
        )
    )
    rec = detail if isinstance(detail, dict) else detail.model_dump()
    return folder_record_to_source(rec)


async def update_folder_source(
    redis: Any,  # noqa: ARG001 — folder handler fetches its own redis
    source_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Patch a watched folder via the router's validated handler and return
    the projected SourceRecord dict. ``path`` in ``config`` is silently
    ignored — folder paths are immutable. 404 propagates from
    ``update_watched_folder`` if the folder does not exist."""
    from app.routers.watched_folders import WatchedFolderUpdate, update_watched_folder

    folder_id = strip_folder_prefix(source_id)
    update_body = WatchedFolderUpdate(
        label=config.get("label"),
        enabled=config.get("enabled"),
        domain_override=config.get("domain_override"),
        exclude_patterns=config.get("exclude_patterns"),
        search_enabled=config.get("search_enabled"),
        is_vault=config.get("is_vault"),
        vault_config=config.get("vault_config"),
    )
    raw = await update_watched_folder(folder_id, update_body)
    rec = raw if isinstance(raw, dict) else raw.model_dump()
    return folder_record_to_source(rec)


def delete_folder_source(redis: Any, source_id: str) -> None:
    """Delete a watched folder by source_id (must include 'folder:' prefix).
    Raises HTTP 404 if the folder does not exist."""
    from fastapi import HTTPException

    from app.routers.watched_folders import _folder_key, _load_folder, _remove_from_index

    fid = strip_folder_prefix(source_id)
    if _load_folder(redis, fid) is None:
        raise HTTPException(status_code=404, detail="Source not found")
    redis.delete(_folder_key(fid))
    _remove_from_index(redis, fid)


async def folder_health(redis: Any, source_id: str) -> dict[str, Any]:
    """Probe folder existence/readability via preview_folder.
    Returns a dict matching HealthProbeResult fields."""
    from app.routers.watched_folders import _load_folder

    fid = strip_folder_prefix(source_id)
    rec = _load_folder(redis, fid)
    if rec is None:
        return {"ok": False, "detail": "folder not found", "last_error": None}

    try:
        from app.services.folder_scanner import preview_folder

        result = await preview_folder(rec["path"])
        if "error" in result:
            return {"ok": False, "detail": result["error"], "last_error": result["error"]}
        return {"ok": True, "detail": f"{result['total_files']} files readable", "last_error": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": "probe failed", "last_error": str(exc)}


def strip_folder_prefix(source_id: str) -> str:
    return source_id[len(_PREFIX):] if source_id.startswith(_PREFIX) else source_id


def folder_record_to_source(rec: dict[str, Any]) -> dict[str, Any]:
    stats = rec.get("stats") or {}
    return {
        "id": f"{_PREFIX}{rec['id']}",
        "kind": "folder",
        "family": "files",
        "display_name": rec.get("label") or rec.get("path", ""),
        "tier": "core",
        "status": "connected" if rec.get("enabled", True) else "paused",
        "config": {
            "path": rec.get("path", ""),
            "exclude_patterns": rec.get("exclude_patterns") or [],
            "is_vault": bool(rec.get("is_vault")),
            "vault_config": rec.get("vault_config"),
            "domain_override": rec.get("domain_override"),
            "search_enabled": rec.get("search_enabled", True),
            "import_mode": rec.get("import_mode") or "watch",
        },
        "sync_cursor": {},
        "total_artifacts": int(stats.get("ingested", 0)),
        "total_chunks": 0,
        "total_edges": 0,
        "total_artifacts_24h": 0,
        "connection_time_ms": None,
        "last_sync_at": rec.get("last_scanned_at"),
        "created_at": rec.get("created_at"),
        "last_error": None,
        "quality_floor": 0.0,
    }


def list_folder_sources(redis: Any) -> list[dict[str, Any]]:
    from app.routers.watched_folders import _list_folder_ids, _load_folder

    out: list[dict[str, Any]] = []
    for fid in _list_folder_ids(redis):
        rec = _load_folder(redis, fid)
        if rec is not None:
            out.append(folder_record_to_source(rec))
    return out


def get_folder_source(redis: Any, source_id: str) -> dict[str, Any] | None:
    from app.routers.watched_folders import _load_folder

    rec = _load_folder(redis, strip_folder_prefix(source_id))
    return folder_record_to_source(rec) if rec is not None else None
