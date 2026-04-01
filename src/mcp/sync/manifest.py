# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sync manifest — write and read manifest.json for cross-machine sync."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import config
from sync._helpers import (
    ARTIFACTS_JSONL,
    AUDIT_LOG_JSONL,
    BM25_SUBDIR,
    CHROMA_SUBDIR,
    DOMAINS_JSONL,
    MANIFEST_FILENAME,
    NEO4J_SUBDIR,
    REDIS_SUBDIR,
    RELATIONSHIPS_JSONL,
    TOMBSTONES_JSONL,
    _count_jsonl_lines,
    _default_sync_dir,
    _sha256_file,
)
from utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.sync")


def write_manifest(
    sync_dir: str | None = None,
    machine_id: str | None = None,
    is_incremental: bool = False,
    last_exported_at: str | None = None,
) -> dict[str, Any]:
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
    tracked_files: list[tuple[str, str]] = [
        # (relative path within sync_dir, absolute path)
        (f"{NEO4J_SUBDIR}/{ARTIFACTS_JSONL}",    str(sync_path / NEO4J_SUBDIR / ARTIFACTS_JSONL)),
        (f"{NEO4J_SUBDIR}/{DOMAINS_JSONL}",       str(sync_path / NEO4J_SUBDIR / DOMAINS_JSONL)),
        (f"{NEO4J_SUBDIR}/{RELATIONSHIPS_JSONL}", str(sync_path / NEO4J_SUBDIR / RELATIONSHIPS_JSONL)),
        (f"{REDIS_SUBDIR}/{AUDIT_LOG_JSONL}",     str(sync_path / REDIS_SUBDIR / AUDIT_LOG_JSONL)),
        (f"{NEO4J_SUBDIR}/{TOMBSTONES_JSONL}",     str(sync_path / NEO4J_SUBDIR / TOMBSTONES_JSONL)),
    ]

    # Add per-domain Chroma files
    for domain in config.DOMAINS:
        coll_name = config.collection_name(domain)
        rel = f"{CHROMA_SUBDIR}/{coll_name}.jsonl"
        tracked_files.append((rel, str(sync_path / CHROMA_SUBDIR / f"{coll_name}.jsonl")))

    # Add BM25 files discovered on disk
    bm25_src = sync_path / BM25_SUBDIR
    if bm25_src.exists():
        for f in sorted(bm25_src.glob("*.jsonl")):
            rel = f"{BM25_SUBDIR}/{f.name}"
            tracked_files.append((rel, str(f)))

    file_entries: dict[str, dict[str, Any]] = {}
    for rel_path, abs_path in tracked_files:
        if not os.path.exists(abs_path):
            file_entries[rel_path] = {"exists": False, "count": 0, "sha256": ""}
            continue
        count = _count_jsonl_lines(abs_path) if abs_path.endswith(".jsonl") else None
        checksum = _sha256_file(abs_path)
        entry: dict[str, Any] = {"exists": True, "sha256": checksum}
        if count is not None:
            entry["count"] = count
        file_entries[rel_path] = entry

    now = utcnow_iso()
    manifest = {
        "machine_id": machine_id,
        "timestamp": now,
        "sync_format_version": 2,
        "last_exported_at": last_exported_at or now,
        "is_incremental": is_incremental,
        "domains": config.DOMAINS,
        "files": file_entries,
    }

    manifest_path = sync_path / MANIFEST_FILENAME
    with open(str(manifest_path), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, default=str)

    logger.info("Manifest written: machine=%s, %d files tracked → %s", machine_id, len(file_entries), manifest_path)
    return manifest


def read_manifest(sync_dir: str | None = None) -> dict[str, Any]:
    """
    Read and parse manifest.json from sync_dir.

    Returns the manifest dict, or raises FileNotFoundError if absent.
    Raises ValueError if the manifest is malformed.
    """
    sync_dir = sync_dir or _default_sync_dir()
    manifest_path = Path(sync_dir) / MANIFEST_FILENAME

    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest found at {manifest_path}")

    with open(str(manifest_path), encoding="utf-8") as fh:
        try:
            manifest = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed manifest.json: {exc}") from exc

    required_keys = {"machine_id", "timestamp", "files"}
    missing = required_keys - set(manifest.keys())
    if missing:
        raise ValueError(f"manifest.json missing required keys: {missing}")

    return manifest
