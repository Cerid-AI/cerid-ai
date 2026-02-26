"""
Sync Backend Abstraction for Cerid AI (Phase 8D).

Defines the interface for sync backends. The current implementation
uses the local filesystem (Dropbox-synced directory). Future backends
(S3, WebDAV, Git) just implement the abstract class.

Usage:
    from utils.sync_backend import get_sync_backend

    backend = get_sync_backend()
    manifest = backend.read_manifest()
    backend.write_file("neo4j/artifacts.jsonl", data)
"""

from __future__ import annotations

import json
import logging
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger("ai-companion.sync")


class SyncBackend(ABC):
    """
    Abstract base class for sync backends.

    All implementations must support basic file operations on the sync
    directory structure. The sync library (cerid_sync_lib.py) uses this
    interface instead of direct pathlib calls.
    """

    @abstractmethod
    def read_manifest(self) -> Optional[Dict[str, Any]]:
        """
        Read the sync manifest file.

        Returns:
            Parsed manifest dict, or None if not found.
        """
        ...

    @abstractmethod
    def write_manifest(self, manifest: Dict[str, Any]) -> None:
        """Write the sync manifest file."""
        ...

    @abstractmethod
    def write_file(self, relative_path: str, data: bytes) -> None:
        """
        Write a file to the sync directory.

        Args:
            relative_path: Path relative to sync root (e.g., "neo4j/artifacts.jsonl")
            data: File content as bytes
        """
        ...

    @abstractmethod
    def read_file(self, relative_path: str) -> Optional[bytes]:
        """
        Read a file from the sync directory.

        Args:
            relative_path: Path relative to sync root

        Returns:
            File content as bytes, or None if not found.
        """
        ...

    @abstractmethod
    def list_files(self, prefix: str = "") -> List[str]:
        """
        List files in the sync directory.

        Args:
            prefix: Optional path prefix to filter by (e.g., "chroma/")

        Returns:
            List of relative paths matching the prefix.
        """
        ...

    @abstractmethod
    def exists(self, relative_path: str) -> bool:
        """Check if a file exists in the sync directory."""
        ...

    @abstractmethod
    def delete_file(self, relative_path: str) -> bool:
        """
        Delete a file from the sync directory.

        Returns True if deleted, False if not found.
        """
        ...

    @abstractmethod
    def ensure_directory(self, relative_path: str) -> None:
        """Create a directory (and parents) in the sync root."""
        ...


class LocalSyncBackend(SyncBackend):
    """
    Local filesystem sync backend.

    Reads/writes to a local directory (typically ~/Dropbox/cerid-sync).
    This is the current default implementation.
    """

    def __init__(self, sync_dir: Optional[str] = None):
        self._root = Path(sync_dir or config.SYNC_DIR)

    @property
    def root(self) -> Path:
        return self._root

    def read_manifest(self) -> Optional[Dict[str, Any]]:
        manifest_path = self._root / "manifest.json"
        if not manifest_path.exists():
            return None
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read manifest: {e}")
            return None

    def write_manifest(self, manifest: Dict[str, Any]) -> None:
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

    def read_file(self, relative_path: str) -> Optional[bytes]:
        full_path = self._root / relative_path
        if not full_path.exists():
            return None
        try:
            return full_path.read_bytes()
        except OSError as e:
            logger.warning(f"Failed to read {relative_path}: {e}")
            return None

    def list_files(self, prefix: str = "") -> List[str]:
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

_BACKENDS: Dict[str, type] = {
    "local": LocalSyncBackend,
}

# Singleton with thread-safe initialization
_active_backend: Optional[SyncBackend] = None
_backend_lock = threading.Lock()


def register_sync_backend(name: str, backend_class: type) -> None:
    """Register a new sync backend type (for plugins)."""
    _BACKENDS[name] = backend_class
    logger.info(f"Registered sync backend: {name}")


def get_sync_backend(
    backend_type: Optional[str] = None,
    sync_dir: Optional[str] = None,
) -> SyncBackend:
    """
    Get the active sync backend instance.

    Thread-safe via double-checked locking.

    Args:
        backend_type: Backend type name (default: "local"). Future: "s3", "webdav", "git"
        sync_dir: Override sync directory (only for local backend)

    Returns:
        SyncBackend instance
    """
    global _active_backend

    backend_type = backend_type or os.getenv("CERID_SYNC_BACKEND", "local")

    if _active_backend is not None and not sync_dir:
        return _active_backend

    with _backend_lock:
        if _active_backend is not None and not sync_dir:
            return _active_backend

        backend_cls = _BACKENDS.get(backend_type)
        if backend_cls is None:
            available = ", ".join(_BACKENDS.keys())
            raise ValueError(
                f"Unknown sync backend: {backend_type}. Available: {available}"
            )

        if backend_type == "local":
            _active_backend = backend_cls(sync_dir=sync_dir)
        else:
            _active_backend = backend_cls()

        return _active_backend


def reset_sync_backend():
    """Reset the singleton (for testing only)."""
    global _active_backend
    with _backend_lock:
        _active_backend = None
