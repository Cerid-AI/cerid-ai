# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Folder-level retrieval exclusion: the watched-folder "Searchable" toggle.

The folder scanner stamps ``watched_folder_id`` on every chunk it writes. A
folder whose ``search_enabled`` is off has its id excluded in the store query
itself, so turning the toggle back on needs no re-ingest and the excluded
chunks never take a top-k slot.

This module defines the filter; ``core.agents.query_agent`` applies it at
every Chroma read on the retrieval path.
"""
from __future__ import annotations

import os
import time
from typing import Any

from core.utils.swallowed import log_swallowed_error
from errors import RetrievalError

WATCHED_FOLDER_KEY = "watched_folder_id"

# The state a folder record carries once the content ingested from it before
# chunks were stamped has been linked back to it.
LINKED = "complete"

# A toggle change in this process invalidates the cache at once; the TTL only
# bounds how long another process can keep serving the previous list.
_CACHE_TTL_S = 30.0
_cache: tuple[float, tuple[str, ...], tuple[str, ...]] | None = None


def _read() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(ids of unsearchable folders, labels of those not yet fully linked)."""
    global _cache
    now = time.monotonic()
    if _cache is not None and (now - _cache[0]) < _CACHE_TTL_S:
        return _cache[1], _cache[2]
    try:
        from app.routers.watched_folders import _get_redis, _list_folder_ids, _load_folder

        redis = _get_redis()
        off = [
            rec for fid in _list_folder_ids(redis)
            if (rec := _load_folder(redis, fid)) and not rec.get("search_enabled", True)
        ]
    except Exception as exc:
        log_swallowed_error("utils.folder_privacy.unsearchable_folder_ids", exc)
        raise RetrievalError(
            "The list of folders excluded from search could not be read, so "
            "the knowledge base was not searched.",
            error_code="RETRIEVAL_FOLDER_EXCLUSIONS_UNREADABLE",
        ) from exc
    ids = tuple(sorted(str(rec["id"]) for rec in off))
    unlinked = tuple(sorted(
        str(rec.get("label") or rec.get("path") or rec["id"])
        for rec in off
        if (rec.get("search_exclusion") or {}).get("state") != LINKED
    ))
    _cache = (now, ids, unlinked)
    return ids, unlinked


def unsearchable_folder_ids() -> tuple[str, ...]:
    """Ids of the watched folders whose Searchable toggle is off.

    Raises ``RetrievalError`` when the folder store cannot be read. Answering
    without the list would return content the user excluded, so the query
    fails instead.
    """
    return _read()[0]


def exclusion_degraded_reason() -> str:
    """User-facing reason when an unsearchable folder is not fully excluded.

    Empty string when every unsearchable folder has had its earlier content
    linked to it, or when the list has not been read in this process.
    """
    if _cache is None or not _cache[2]:
        return ""
    return (
        "These folders are marked not searchable, but files ingested from "
        "them earlier have not all been matched to them yet and may still "
        "appear in results: " + ", ".join(_cache[2]) + "."
    )


def invalidate_unsearchable_folders() -> None:
    """Drop the cached list. Called when a folder is created, changed or removed."""
    global _cache
    _cache = None


def exclude_folders(where: dict | None, folder_ids: tuple[str, ...]) -> dict | None:
    """Fuse the folder exclusion into a Chroma ``where`` clause.

    ``$nin`` also matches chunks that carry no ``watched_folder_id`` at all,
    so content that never came from a watched folder is unaffected.
    """
    if not folder_ids:
        return where
    excl = {WATCHED_FOLDER_KEY: {"$nin": list(folder_ids)}}
    if where is None:
        return excl
    if "$and" in where:
        return {"$and": [*where["$and"], excl]}
    return {"$and": [where, excl]}


def chunk_excluded(metadata: dict[str, Any] | None, folder_ids: tuple[str, ...]) -> bool:
    """The same exclusion for a chunk fetched by id, where no ``where`` ran."""
    return bool(folder_ids) and (metadata or {}).get(WATCHED_FOLDER_KEY) in folder_ids


def owning_folder_id(file_path: str, folders: list[dict[str, Any]]) -> str | None:
    """The watched folder a file belongs to: the deepest one that contains it.

    Compared by path component, so ``/a/b`` does not claim ``/a/bc/x.md``.
    """
    path = os.path.normpath(file_path)
    best: tuple[int, str] | None = None
    for rec in folders:
        root = os.path.normpath(str(rec.get("path") or ""))
        if not root or not rec.get("id"):
            continue
        try:
            inside = os.path.commonpath([path, root]) == root
        except ValueError:
            continue
        if inside and (best is None or len(root) > best[0]):
            best = (len(root), str(rec["id"]))
    return best[1] if best else None
