# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sync backend abstraction — pluggable backends for knowledge base sync."""

from __future__ import annotations

import json
import logging
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger("ai-companion.sync")


class SyncBackend(ABC):
    """Abstract base class for sync backends."""

    @abstractmethod
    def read_manifest(self) -> dict[str, Any] | None:
        """Read the sync manifest file."""
        ...

    @abstractmethod
    def write_manifest(self, manifest: dict[str, Any]) -> None:
        """Write the sync manifest file."""
        ...

    @abstractmethod
    def write_file(self, relative_path: str, data: bytes) -> None:
        """Write a file to the sync directory."""
        ...

    @abstractmethod
    def read_file(self, relative_path: str) -> bytes | None:
        """Read a file from the sync directory."""
        ...

    @abstractmethod
    def list_files(self, prefix: str = "") -> list[str]:
        """List files in the sync directory, optionally filtered by prefix."""
        ...

    @abstractmethod
    def exists(self, relative_path: str) -> bool:
        """Check if a file exists in the sync directory."""
        ...

    @abstractmethod
    def delete_file(self, relative_path: str) -> bool:
        """Delete a file. Returns True if deleted, False if not found."""
        ...

    @abstractmethod
    def ensure_directory(self, relative_path: str) -> None:
        """Create a directory (and parents) in the sync root."""
        ...


class LocalSyncBackend(SyncBackend):
    """Local filesystem sync backend (default — typically ~/Dropbox/cerid-sync)."""

    def __init__(self, sync_dir: str | None = None):
        self._root = Path(sync_dir or config.SYNC_DIR)

    @property
    def root(self) -> Path:
        return self._root

    def read_manifest(self) -> dict[str, Any] | None:
        manifest_path = self._root / "manifest.json"
        if not manifest_path.exists():
            return None
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read manifest: {e}")
            return None

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        self.ensure_directory("")
        manifest_path = self._root / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, default=str),
            encoding="utf-8",
        )

    def write_file(self, relative_path: str, data: bytes) -> None:
        full_path = self._root / relative_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(data)

    def read_file(self, relative_path: str) -> bytes | None:
        full_path = self._root / relative_path
        if not full_path.exists():
            return None
        try:
            return full_path.read_bytes()
        except OSError as e:
            logger.warning(f"Failed to read {relative_path}: {e}")
            return None

    def list_files(self, prefix: str = "") -> list[str]:
        search_dir = self._root / prefix if prefix else self._root
        if not search_dir.exists():
            return []

        results = []
        for path in search_dir.rglob("*"):
            if path.is_file():
                results.append(str(path.relative_to(self._root)))
        return sorted(results)

    def exists(self, relative_path: str) -> bool:
        return (self._root / relative_path).exists()

    def delete_file(self, relative_path: str) -> bool:
        full_path = self._root / relative_path
        if full_path.exists():
            full_path.unlink()
            return True
        return False

    def ensure_directory(self, relative_path: str) -> None:
        dir_path = self._root / relative_path if relative_path else self._root
        dir_path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Backend registry
# ---------------------------------------------------------------------------

_BACKENDS: dict[str, type] = {
    "local": LocalSyncBackend,
}

_active_backend: SyncBackend | None = None
_backend_lock = threading.Lock()


def register_sync_backend(name: str, backend_class: type) -> None:
    """Register a new sync backend type (for plugins)."""
    _BACKENDS[name] = backend_class
    logger.info(f"Registered sync backend: {name}")


def get_sync_backend(
    backend_type: str | None = None,
    sync_dir: str | None = None,
) -> SyncBackend:
    """Get the active sync backend singleton (thread-safe)."""
    global _active_backend

    resolved_type = backend_type if backend_type else os.getenv("CERID_SYNC_BACKEND", "local")

    if _active_backend is not None and not sync_dir:
        return _active_backend

    with _backend_lock:
        if _active_backend is not None and not sync_dir:
            return _active_backend

        backend_cls = _BACKENDS.get(resolved_type)
        if backend_cls is None:
            available = ", ".join(_BACKENDS.keys())
            raise ValueError(
                f"Unknown sync backend: {resolved_type}. Available: {available}"
            )

        if resolved_type == "local":
            _active_backend = backend_cls(sync_dir=sync_dir)
        else:
            _active_backend = backend_cls()

        return _active_backend


def reset_sync_backend():
    """Reset the singleton (for testing only)."""
    global _active_backend
    with _backend_lock:
        _active_backend = None
