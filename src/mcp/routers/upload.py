# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""File upload endpoint for GUI-based ingestion."""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

import config
from routers.ingestion import ingest_file

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

    # Save to a temp directory inside the archive path so it passes path validation
    archive_root = Path(config.ARCHIVE_PATH)
    upload_dir = archive_root / "_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = None
    try:
        # Use tempfile to avoid collisions, but keep the original extension
        fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="upload_", dir=str(upload_dir))
        os.write(fd, content)
        os.close(fd)

        logger.info(f"Upload received: {file.filename} ({len(content)} bytes) -> {tmp_path}")

        result = await ingest_file(
            file_path=tmp_path,
            domain=domain,
            sub_category=sub_category,
            tags=tags,
            categorize_mode=categorize_mode,
        )

        # Override filename in result with the original upload name
        result["filename"] = file.filename

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