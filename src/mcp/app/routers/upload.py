# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""File upload endpoint for GUI-based ingestion."""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

import config

router = APIRouter()
logger = logging.getLogger("ai-companion.upload")

# Maximum upload size: 50 MB
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_file_endpoint(
    file: UploadFile = File(...),
    domain: str = Query("", description="Target domain (empty = auto-detect)"),
    sub_category: str = Query("", description="Sub-category within domain"),
    tags: str = Query("", description="Comma-separated tags or JSON array"),
    categorize_mode: str = Query("", description="Categorization tier: manual, smart, or pro"),
):
    """Accept a multipart file upload, validate it, and ingest into the knowledge base.

    The file is saved to a temporary directory under the configured archive path
    so it passes the path validation in the ingestion pipeline. After ingestion
    the temp file is cleaned up.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    # Validate file extension
    suffix = Path(file.filename).suffix.lower()
    if suffix not in config.SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: '{suffix}'. "
            f"Supported: {sorted(config.SUPPORTED_EXTENSIONS)}",
        )

    # Read file content with size limit
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {len(content)} bytes. Maximum: {MAX_UPLOAD_BYTES} bytes (50 MB)",
        )
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    # Save to /tmp (always writable) — the archive mount may be read-only on macOS Docker
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="upload_")
        os.write(fd, content)
        os.close(fd)

        logger.info(f"Upload received: {file.filename} ({len(content)} bytes) -> {tmp_path}")

        # Parse file from /tmp, then ingest the extracted text content
        # (bypasses archive path validation which requires files under ARCHIVE_PATH)
        from app.parsers import parse_file as _parse_file
        from app.services.ingestion import ingest_content

        parsed = _parse_file(tmp_path)
        text = parsed.get("text", "")
        if not text.strip():
            raise ValueError(f"No text extracted from '{file.filename}'")

        metadata = {
            "filename": file.filename,
            "file_type": parsed.get("file_type", ""),
            "sub_category": sub_category,
            "client_source": "upload",
        }
        # Add optional parsed fields, filtering out None (ChromaDB rejects None)
        for key in ("page_count", "table_count", "form_field_count"):
            val = parsed.get(key)
            if val is not None:
                metadata[key] = val
        result = ingest_content(
            text,
            domain or "general",
            metadata,
        )
        result["categorize_mode"] = categorize_mode or "smart"
        result["metadata"] = metadata

        # Override filename in result with the original upload name
        result["filename"] = file.filename

        # Archive mode: copy the file to archive/{domain}/ for Dropbox sync
        if config.STORAGE_MODE == "archive" and tmp_path:
            _archive_file(tmp_path, file.filename, result.get("domain", domain or "general"))

        return result

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload ingest error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Clean up temp file
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError as e:
                logger.warning(f"Failed to clean up temp file {tmp_path}: {e}")


@router.get("/upload/supported")
async def supported_extensions_endpoint():
    """Return the list of supported file extensions for upload."""
    return {
        "extensions": sorted(config.SUPPORTED_EXTENSIONS),
        "count": len(config.SUPPORTED_EXTENSIONS),
    }


@router.get("/archive/files")
async def list_archive_files(
    domain: str = Query("", description="Filter by domain folder (empty = all)"),
):
    """List files in the archive directory, grouped by domain folder."""
    archive_root = Path(config.ARCHIVE_PATH)
    if not archive_root.exists():
        return {"files": [], "total": 0, "storage_mode": config.STORAGE_MODE}

    files: list[dict[str, str | int]] = []
    scan_dirs = [archive_root / domain] if domain else [
        d for d in sorted(archive_root.iterdir())
        if d.is_dir() and not d.name.startswith(("_", "."))
    ]

    for domain_dir in scan_dirs:
        if not domain_dir.is_dir():
            continue
        domain_name = domain_dir.name
        for entry in sorted(domain_dir.iterdir()):
            if entry.is_file() and not entry.name.startswith("."):
                files.append({
                    "filename": entry.name,
                    "domain": domain_name,
                    "size": entry.stat().st_size,
                    "path": str(entry.relative_to(archive_root)),
                })

    return {
        "files": files,
        "total": len(files),
        "storage_mode": config.STORAGE_MODE,
    }


def _archive_file(tmp_path: str, original_filename: str, domain: str) -> None:
    """Copy an uploaded file to archive/{domain}/ for persistent storage."""
    archive_root = Path(config.ARCHIVE_PATH)
    dest_dir = archive_root / domain
    dest_dir.mkdir(parents=True, exist_ok=True)
    original_filename = Path(original_filename).name  # Strip directory components (path traversal prevention)
    dest = dest_dir / original_filename

    # Avoid overwriting — add numeric suffix if file exists
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        counter = 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    try:
        shutil.copy2(tmp_path, str(dest))
        logger.info(f"Archived upload: {original_filename} -> {dest}")
    except OSError as e:
        logger.warning(f"Failed to archive {original_filename}: {e}")
