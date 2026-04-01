# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Autonomous Folder Scanner — ingests new files from configured directories.

Walks directory trees, deduplicates via content hash (Redis), and ingests
new files through the standard ingestion pipeline.  Designed for both
scheduled background runs and on-demand API-triggered scans.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

import config
from config.taxonomy import SUPPORTED_EXTENSIONS
from deps import get_redis
from errors import IngestionError
from parsers import parse_file as _parse_file
from services.ingestion import ingest_content, ingest_file
from utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.scanner")


@dataclass
class ScanResult:
    path: str
    status: str  # "ingested" | "duplicate" | "low_quality" | "error" | "skipped" | "unsupported" | "preview"
    quality_score: float = 0.0
    domain: str = ""
    artifact_id: str = ""
    error_msg: str = ""
    file_size_bytes: int = 0


DEFAULT_EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", "venv", ".venv", ".env", ".tox",
    "dist", "build", ".cache", ".pytest_cache", ".mypy_cache", "egg-info",
    "__MACOSX", ".Spotlight-V100", ".Trashes", ".fseventsd", ".TemporaryItems",
    "Library", "Caches", ".npm", ".yarn", "coverage",
}

# Files to always skip (junk, temp, system files)
JUNK_FILE_PATTERNS = {
    ".ds_store", "thumbs.db", "desktop.ini", ".gitignore", ".gitkeep",
    ".npmrc", ".yarnrc", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
}

JUNK_FILE_PREFIXES = ("~$", "._")  # Office temp files, macOS resource forks

ARCHIVE_EXTENSIONS = {".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".7z", ".rar", ".gz", ".bz2"}

# Redis key prefixes
_KEY_PREFIX = "cerid:scan"
_KEY_FILES = f"{_KEY_PREFIX}:files"
_KEY_TOTAL_INGESTED = f"{_KEY_PREFIX}:total_ingested"
_KEY_TOTAL_SKIPPED = f"{_KEY_PREFIX}:total_skipped"
_KEY_TOTAL_ERRORED = f"{_KEY_PREFIX}:total_errored"
_KEY_LAST_SCAN_AT = f"{_KEY_PREFIX}:last_scan_at"

_FILE_HASH_TTL = 2_592_000  # 30 days in seconds


def _file_content_hash(path: str) -> str:
    """Compute SHA-256 hash of file content using 64KB chunked reads."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65_536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _estimate_file_count(
    root: Path,
    extensions: set[str],
    exclude_dirs: set[str],
) -> int:
    """Quick recursive count of matching files without reading content."""
    count = 0
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    name = entry.name
                    if name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if name.lower() not in exclude_dirs:
                            stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        suffix = Path(name).suffix.lower()
                        if suffix in extensions:
                            count += 1
        except PermissionError:
            continue
    return count


def _detect_domain_from_path(file_path: str, root_path: str) -> tuple[str, str]:
    """Infer domain and sub-category from directory structure.

    root/coding/python/file.py → ("coding", "python")
    root/coding/file.py        → ("coding", "")
    root/file.py               → ("", "")
    """
    root = root_path.rstrip("/")
    rel = os.path.relpath(file_path, root)
    parts = rel.split(os.sep)
    if len(parts) >= 2:
        folder = parts[0].lower()
        if folder in config.DOMAINS:
            if len(parts) >= 3:
                sub_cat = parts[1].lower()
                domain_info = config.TAXONOMY.get(folder, {})
                valid_subs = [s.lower() for s in domain_info.get("sub_categories", [])]
                if sub_cat in valid_subs:
                    return folder, sub_cat
            return folder, ""
    return "", ""


def _record_file_scanned(
    redis: Any,
    content_hash: str,
    path: str,
    status: str,
) -> None:
    """Record a scanned file in Redis and update counters."""
    redis.set(
        f"{_KEY_FILES}:{content_hash}",
        json.dumps({"path": path, "status": status, "scanned_at": utcnow_iso()}),
        ex=_FILE_HASH_TTL,
    )
    if status == "ingested":
        redis.incr(_KEY_TOTAL_INGESTED)
    elif status in ("duplicate", "low_quality", "skipped", "unsupported"):
        redis.incr(_KEY_TOTAL_SKIPPED)
    elif status == "error":
        redis.incr(_KEY_TOTAL_ERRORED)
    redis.set(_KEY_LAST_SCAN_AT, utcnow_iso())


async def _extract_and_scan_archive(
    archive_path: str,
    root_path: str,
    valid_extensions: set[str],
    exclude_dirs: set[str],
    max_size: int,
    min_quality: float,
    dry_run: bool,
    sem: asyncio.Semaphore,
) -> AsyncIterator[ScanResult]:
    """Extract a zip/tar archive to a temp dir and scan its contents."""
    import shutil
    import tarfile
    import tempfile
    import zipfile

    archive_size = os.path.getsize(archive_path)
    max_archive = 500 * 1024 * 1024  # 500MB
    if archive_size > max_archive:
        yield ScanResult(
            path=archive_path, status="skipped",
            file_size_bytes=archive_size,
            error_msg=f"archive too large ({archive_size // (1024*1024)}MB > 500MB limit)",
        )
        return

    tmp_dir = tempfile.mkdtemp(prefix="cerid-extract-")
    extracted_count = 0
    try:
        # Extract
        name_lower = os.path.basename(archive_path).lower()
        if name_lower.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                # Security: check for path traversal
                for info in zf.infolist():
                    if ".." in info.filename or info.filename.startswith("/"):
                        continue
                    zf.extract(info, tmp_dir)
                    extracted_count += 1
        elif name_lower.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar")):
            with tarfile.open(archive_path, "r:*") as tf:
                for member in tf.getmembers():
                    if ".." in member.name or member.name.startswith("/"):
                        continue
                    if member.isfile():
                        tf.extract(member, tmp_dir, filter="data")
                        extracted_count += 1
        else:
            yield ScanResult(path=archive_path, status="skipped", error_msg="unsupported archive format")
            return

        if extracted_count == 0:
            yield ScanResult(path=archive_path, status="skipped", error_msg="empty archive")
            return

        # Scan extracted files (reuse scan_folder logic inline)
        redis = get_redis()
        for dirpath, _, filenames in os.walk(tmp_dir):
            dir_name = os.path.basename(dirpath).lower()
            if dir_name in exclude_dirs or dir_name.startswith("."):
                continue
            for fname in filenames:
                if fname.startswith(".") or fname.lower() in JUNK_FILE_PATTERNS:
                    continue
                suffix = Path(fname).suffix.lower()
                if suffix not in valid_extensions:
                    continue

                fpath = os.path.join(dirpath, fname)
                try:
                    fsize = os.path.getsize(fpath)
                except OSError:
                    continue
                if fsize > max_size or fsize == 0:
                    continue

                if dry_run:
                    domain, sub_cat = _detect_domain_from_path(fpath, tmp_dir)
                    yield ScanResult(
                        path=f"{archive_path}:{fname}",
                        status="preview", domain=domain, file_size_bytes=fsize,
                    )
                    continue

                # Dedup check
                try:
                    content_hash = await asyncio.to_thread(_file_content_hash, fpath)
                except OSError:
                    continue
                if redis.get(f"{_KEY_FILES}:{content_hash}"):
                    yield ScanResult(path=f"{archive_path}:{fname}", status="duplicate", file_size_bytes=fsize)
                    continue

                domain, sub_cat = _detect_domain_from_path(fpath, tmp_dir)
                async with sem:
                    try:
                        parsed = await asyncio.to_thread(_parse_file, fpath)
                        text = parsed.get("text", "")
                        if not text.strip():
                            _record_file_scanned(redis, content_hash, fpath, "low_quality")
                            yield ScanResult(path=f"{archive_path}:{fname}", status="low_quality", domain=domain, file_size_bytes=fsize)
                            continue
                        result = await asyncio.to_thread(
                            ingest_content, text, domain or "general",
                            {"filename": fname, "sub_category": sub_cat, "client_source": f"archive:{os.path.basename(archive_path)}"},
                        )
                        quality = result.get("quality_score", 0.0)
                        if quality < min_quality:
                            _record_file_scanned(redis, content_hash, fpath, "low_quality")
                            yield ScanResult(path=f"{archive_path}:{fname}", status="low_quality", quality_score=quality, domain=domain, file_size_bytes=fsize)
                        else:
                            _record_file_scanned(redis, content_hash, fpath, "ingested")
                            yield ScanResult(
                                path=f"{archive_path}:{fname}", status="ingested",
                                quality_score=quality, domain=domain,
                                artifact_id=result.get("artifact_id", ""), file_size_bytes=fsize,
                            )
                    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
                        _record_file_scanned(redis, content_hash, fpath, "error")
                        yield ScanResult(path=f"{archive_path}:{fname}", status="error", error_msg=str(e), file_size_bytes=fsize)
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        yield ScanResult(path=archive_path, status="error", error_msg=f"archive extraction failed: {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def scan_folder(
    root_path: str,
    *,
    min_quality: float = 0.4,
    extensions: set[str] | None = None,
    exclude_patterns: set[str] | None = None,
    max_file_size_mb: int = 50,
    dry_run: bool = False,
) -> AsyncIterator[ScanResult]:
    """Async generator that walks a directory tree and ingests new files.

    Yields a ScanResult for each file encountered (ingested, skipped, error, etc.).
    Uses Redis content-hash tracking to avoid re-processing already-ingested files.
    """
    root = Path(root_path)
    if not root.is_dir():
        logger.error(f"Scan root is not a directory: {root_path}")
        return

    valid_extensions = extensions or SUPPORTED_EXTENSIONS
    exclude_dirs = DEFAULT_EXCLUDE_DIRS | (exclude_patterns or set())
    max_size = max_file_size_mb * 1024 * 1024
    redis = get_redis()
    sem = asyncio.Semaphore(3)

    # Stack-based recursive walk using os.scandir
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except PermissionError:
            logger.warning(f"Permission denied: {current}")
            continue

        for entry in entries:
            name = entry.name

            # Skip hidden files/dirs
            if name.startswith("."):
                continue

            if entry.is_dir(follow_symlinks=False):
                if name.lower() not in exclude_dirs:
                    stack.append(Path(entry.path))
                continue

            if not entry.is_file(follow_symlinks=False):
                continue

            file_path = entry.path
            suffix = Path(name).suffix.lower()
            name_lower = name.lower()

            # Skip junk files
            if name_lower in JUNK_FILE_PATTERNS or any(name.startswith(p) for p in JUNK_FILE_PREFIXES):
                yield ScanResult(path=file_path, status="skipped", error_msg="junk file")
                continue

            # Extract archives and process contents
            if suffix in ARCHIVE_EXTENSIONS or name_lower.endswith((".tar.gz", ".tar.bz2")):
                async for r in _extract_and_scan_archive(
                    file_path, root_path, valid_extensions, exclude_dirs,
                    max_size, min_quality, dry_run, sem,
                ):
                    yield r
                continue

            # Check extension
            if suffix not in valid_extensions:
                yield ScanResult(path=file_path, status="unsupported")
                continue

            # Check file size
            try:
                stat = entry.stat(follow_symlinks=False)
                file_size = stat.st_size
            except OSError:
                yield ScanResult(path=file_path, status="error", error_msg="stat failed")
                continue

            if file_size > max_size:
                yield ScanResult(
                    path=file_path,
                    status="skipped",
                    file_size_bytes=file_size,
                    error_msg=f"exceeds {max_file_size_mb}MB limit",
                )
                continue

            # Content hash dedup check
            try:
                content_hash = await asyncio.to_thread(_file_content_hash, file_path)
            except OSError as e:
                yield ScanResult(path=file_path, status="error", error_msg=str(e))
                continue

            existing = redis.get(f"{_KEY_FILES}:{content_hash}")
            if existing:
                yield ScanResult(path=file_path, status="duplicate", file_size_bytes=file_size)
                continue

            # Dry run — preview only
            if dry_run:
                domain, sub_cat = _detect_domain_from_path(file_path, root_path)
                yield ScanResult(
                    path=file_path,
                    status="preview",
                    domain=domain,
                    file_size_bytes=file_size,
                )
                continue

            # Ingest with concurrency limit
            domain, sub_cat = _detect_domain_from_path(file_path, root_path)

            async with sem:
                try:
                    # Try ingest_file first; fall back to parse+ingest_content
                    # if Path.resolve() fails on Docker read-only overlay mounts.
                    try:
                        result = await ingest_file(
                            file_path=file_path,
                            domain=domain,
                            sub_category=sub_cat,
                            client_source="folder_scanner",
                        )
                    except (OSError, ValueError):
                        # Fallback: parse file content, then ingest as text
                        parsed = await asyncio.to_thread(_parse_file, file_path)
                        text = parsed.get("text", "")
                        if not text.strip():
                            _record_file_scanned(redis, content_hash, file_path, "low_quality")
                            yield ScanResult(path=file_path, status="low_quality", domain=domain, file_size_bytes=file_size)
                            continue
                        filename = Path(file_path).name
                        result = await asyncio.to_thread(
                            ingest_content,
                            text,
                            domain or "general",
                            {"filename": filename, "sub_category": sub_cat, "client_source": "folder_scanner"},
                        )
                    artifact_id = result.get("artifact_id", "")
                    quality = result.get("quality_score", 0.0)

                    if result.get("duplicate"):
                        _record_file_scanned(redis, content_hash, file_path, "duplicate")
                        yield ScanResult(
                            path=file_path,
                            status="duplicate",
                            file_size_bytes=file_size,
                            domain=domain,
                        )
                    elif quality < min_quality:
                        _record_file_scanned(redis, content_hash, file_path, "low_quality")
                        yield ScanResult(
                            path=file_path,
                            status="low_quality",
                            quality_score=quality,
                            domain=domain,
                            file_size_bytes=file_size,
                        )
                    else:
                        _record_file_scanned(redis, content_hash, file_path, "ingested")
                        yield ScanResult(
                            path=file_path,
                            status="ingested",
                            quality_score=quality,
                            domain=domain,
                            artifact_id=artifact_id,
                            file_size_bytes=file_size,
                        )
                except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
                    _record_file_scanned(redis, content_hash, file_path, "error")
                    logger.error(f"Scan ingest failed for {file_path}: {e}")
                    yield ScanResult(
                        path=file_path,
                        status="error",
                        error_msg=str(e),
                        file_size_bytes=file_size,
                    )


async def preview_folder(
    root_path: str,
    *,
    extensions: set[str] | None = None,
    exclude_patterns: set[str] | None = None,
    max_file_size_mb: int = 50,
) -> dict:
    """Quick scan returning file counts and size breakdown without ingestion."""
    root = Path(root_path)
    if not root.is_dir():
        return {"error": f"Not a directory: {root_path}"}

    valid_extensions = extensions or SUPPORTED_EXTENSIONS
    exclude_dirs = DEFAULT_EXCLUDE_DIRS | (exclude_patterns or set())
    max_size = max_file_size_mb * 1024 * 1024

    total_files = 0
    total_size = 0
    by_extension: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    junk_count = 0
    archive_count = 0
    oversized_count = 0
    unsupported_count = 0
    total_scanned = 0

    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    name = entry.name
                    if name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if name.lower() not in exclude_dirs:
                            stack.append(Path(entry.path))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue

                    total_scanned += 1
                    name_lower = name.lower()
                    suffix = Path(name).suffix.lower()

                    # Junk detection
                    if name_lower in JUNK_FILE_PATTERNS or any(name.startswith(p) for p in JUNK_FILE_PREFIXES):
                        junk_count += 1
                        continue

                    # Archive detection
                    if suffix in ARCHIVE_EXTENSIONS or name_lower.endswith((".tar.gz", ".tar.bz2")):
                        archive_count += 1
                        continue

                    # Unsupported extension
                    if suffix not in valid_extensions:
                        unsupported_count += 1
                        continue

                    try:
                        stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue

                    if stat.st_size > max_size:
                        oversized_count += 1
                        continue

                    total_files += 1
                    total_size += stat.st_size
                    by_extension[suffix] = by_extension.get(suffix, 0) + 1

                    domain, _ = _detect_domain_from_path(entry.path, root_path)
                    domain = domain or "unclassified"
                    by_domain[domain] = by_domain.get(domain, 0) + 1
        except PermissionError:
            continue

    # Rough chunk estimate: ~500 tokens per chunk, ~4 chars per token, ~2000 chars per chunk
    estimated_chunks = int((total_size / 2000) * 1.2) if total_size else 0  # 20% overlap
    # Estimated storage: ~3KB per chunk (vector + metadata)
    estimated_storage_mb = round(estimated_chunks * 3 / 1024, 1)

    return {
        "total_files": total_files,
        "total_scanned": total_scanned,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "by_extension": dict(sorted(by_extension.items(), key=lambda x: -x[1])),
        "by_domain": dict(sorted(by_domain.items(), key=lambda x: -x[1])),
        "estimated_chunks": estimated_chunks,
        "estimated_storage_mb": estimated_storage_mb,
        "skipped": {
            "junk": junk_count,
            "archives": archive_count,
            "unsupported": unsupported_count,
            "oversized": oversized_count,
        },
    }


def get_scan_state(redis: Any) -> dict:
    """Return persistent scan state from Redis."""
    return {
        "total_ingested": int(redis.get(_KEY_TOTAL_INGESTED) or 0),
        "total_skipped": int(redis.get(_KEY_TOTAL_SKIPPED) or 0),
        "total_errored": int(redis.get(_KEY_TOTAL_ERRORED) or 0),
        "last_scan_at": (redis.get(_KEY_LAST_SCAN_AT) or b"").decode()
        if isinstance(redis.get(_KEY_LAST_SCAN_AT), bytes)
        else redis.get(_KEY_LAST_SCAN_AT) or None,
    }


def clear_scan_state(redis: Any) -> int:
    """Delete all cerid:scan:* keys using SCAN (not KEYS). Returns count deleted."""
    deleted = 0
    cursor = 0
    while True:
        cursor, keys = redis.scan(cursor=cursor, match=f"{_KEY_PREFIX}:*", count=200)
        if keys:
            deleted += redis.delete(*keys)
        if cursor == 0:
            break
    return deleted
