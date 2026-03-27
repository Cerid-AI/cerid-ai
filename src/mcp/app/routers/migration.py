# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Migration REST endpoints — import from Notion and Obsidian exports.

Accepts uploaded ZIP files, parses them asynchronously, and tracks progress
in Redis so the GUI can poll for status.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, File
from pydantic import BaseModel

router = APIRouter(prefix="/api/migrate", tags=["migration"])

logger = logging.getLogger("ai-companion.migration")

_MIGRATION_KEY_PREFIX = "cerid:migration:"


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class MigrationStartResponse(BaseModel):
    job_id: str
    pages_found: int = 0
    notes_found: int = 0


class MigrationStatusResponse(BaseModel):
    job_id: str
    status: str
    total: int = 0
    processed: int = 0
    errors: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_redis() -> Any:
    """Deferred import of Redis client."""
    from deps import get_redis_sync
    return get_redis_sync()


def _set_migration_status(
    job_id: str,
    *,
    status: str,
    total: int = 0,
    processed: int = 0,
    errors: int = 0,
) -> None:
    """Update migration progress in a Redis hash."""
    redis = _get_redis()
    if redis is None:
        return
    key = f"{_MIGRATION_KEY_PREFIX}{job_id}"
    redis.hset(key, mapping={
        "status": status,
        "total": str(total),
        "processed": str(processed),
        "errors": str(errors),
    })
    redis.expire(key, 86400)  # 24h TTL


def _process_notion_pages(job_id: str, pages: list[dict[str, Any]]) -> None:
    """Background task: ingest parsed Notion pages into the knowledge base."""
    total = len(pages)
    _set_migration_status(job_id, status="processing", total=total)

    processed = 0
    errors = 0

    for page in pages:
        try:
            from services.ingestion import ingest_content
            ingest_content(
                content=page["content"],
                title=page["title"],
                domain="domain_projects",
                source="notion",
                metadata=page.get("metadata", {}),
            )
            processed += 1
        except Exception as exc:
            logger.warning("Notion ingest error for '%s': %s", page.get("title", "?"), exc)
            errors += 1

        _set_migration_status(job_id, status="processing", total=total, processed=processed, errors=errors)

    _set_migration_status(job_id, status="completed", total=total, processed=processed, errors=errors)
    logger.info("Notion migration %s complete: %d/%d processed, %d errors", job_id, processed, total, errors)


def _process_obsidian_notes(job_id: str, notes: list[dict[str, Any]]) -> None:
    """Background task: ingest parsed Obsidian notes into the knowledge base."""
    total = len(notes)
    _set_migration_status(job_id, status="processing", total=total)

    processed = 0
    errors = 0

    for note in notes:
        try:
            from services.ingestion import ingest_content
            metadata = note.get("metadata", {})
            # Store wiki-links as metadata for relationship building
            links = note.get("links", [])
            if links:
                import json
                metadata["wiki_links_json"] = json.dumps(links)

            ingest_content(
                content=note["content"],
                title=note["title"],
                domain=metadata.get("domain", "domain_projects"),
                source="obsidian",
                metadata=metadata,
            )
            processed += 1
        except Exception as exc:
            logger.warning("Obsidian ingest error for '%s': %s", note.get("title", "?"), exc)
            errors += 1

        _set_migration_status(job_id, status="processing", total=total, processed=processed, errors=errors)

    _set_migration_status(job_id, status="completed", total=total, processed=processed, errors=errors)
    logger.info("Obsidian migration %s complete: %d/%d processed, %d errors", job_id, processed, total, errors)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/notion", response_model=MigrationStartResponse)
async def migrate_notion(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Notion export ZIP file"),
) -> MigrationStartResponse:
    """Upload a Notion export ZIP and start background migration."""
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="File must be a .zip archive")

    job_id = str(uuid.uuid4())

    # Save upload to temp file
    tmp_dir = Path(tempfile.mkdtemp(prefix="cerid_notion_"))
    zip_path = tmp_dir / "notion_export.zip"
    try:
        content = await file.read()
        zip_path.write_bytes(content)

        if not zipfile.is_zipfile(zip_path):
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail="Uploaded file is not a valid ZIP archive")

        from app.parsers.notion import parse_notion_export
        pages = parse_notion_export(zip_path)
    except HTTPException:
        raise
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=f"Failed to parse Notion export: {exc}") from exc

    _set_migration_status(job_id, status="queued", total=len(pages))
    background_tasks.add_task(_process_notion_pages, job_id, pages)
    background_tasks.add_task(shutil.rmtree, tmp_dir, True)

    return MigrationStartResponse(job_id=job_id, pages_found=len(pages))


@router.post("/obsidian", response_model=MigrationStartResponse)
async def migrate_obsidian(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Obsidian vault ZIP file"),
) -> MigrationStartResponse:
    """Upload an Obsidian vault ZIP and start background migration."""
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="File must be a .zip archive")

    job_id = str(uuid.uuid4())

    tmp_dir = Path(tempfile.mkdtemp(prefix="cerid_obsidian_"))
    zip_path = tmp_dir / "obsidian_vault.zip"
    extract_dir = tmp_dir / "vault"
    try:
        content = await file.read()
        zip_path.write_bytes(content)

        if not zipfile.is_zipfile(zip_path):
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail="Uploaded file is not a valid ZIP archive")

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        from app.parsers.obsidian import parse_obsidian_vault
        notes = parse_obsidian_vault(extract_dir)
    except HTTPException:
        raise
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=f"Failed to parse Obsidian vault: {exc}") from exc

    _set_migration_status(job_id, status="queued", total=len(notes))
    background_tasks.add_task(_process_obsidian_notes, job_id, notes)
    background_tasks.add_task(shutil.rmtree, tmp_dir, True)

    return MigrationStartResponse(job_id=job_id, notes_found=len(notes))


@router.get("/status/{job_id}", response_model=MigrationStatusResponse)
async def migration_status(job_id: str) -> MigrationStatusResponse:
    """Check migration progress for a given job."""
    redis = _get_redis()
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    key = f"{_MIGRATION_KEY_PREFIX}{job_id}"
    data = redis.hgetall(key)

    if not data:
        raise HTTPException(status_code=404, detail=f"Migration job not found: {job_id}")

    # Redis returns bytes or strings depending on decode_responses
    def _val(k: str, default: str = "0") -> str:
        v = data.get(k, data.get(k.encode(), default))
        return v.decode() if isinstance(v, bytes) else str(v)

    return MigrationStatusResponse(
        job_id=job_id,
        status=_val("status", "unknown"),
        total=int(_val("total")),
        processed=int(_val("processed")),
        errors=int(_val("errors")),
    )
