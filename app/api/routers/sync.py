# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sync REST endpoints — trigger export/import/status via HTTP.

Business logic lives in sync/ package.
This module is a thin router: Pydantic models + endpoint handlers.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import config
from deps import get_neo4j, get_redis

router = APIRouter()
logger = logging.getLogger("ai-companion.sync")

# Concurrency limiter for sync operations
_sync_semaphore = asyncio.Semaphore(1)


# -- Pydantic models ----------------------------------------------------------

class ExportRequest(BaseModel):
    sync_dir: str | None = None
    machine_id: str | None = None
    since: str | None = None  # ISO-8601 for incremental (None = auto from manifest)
    domains: list[str] | None = None


class ImportRequest(BaseModel):
    sync_dir: str | None = None
    force: bool = False
    conflict_strategy: str = "remote_wins"


# -- Endpoints -----------------------------------------------------------------

@router.post("/sync/export")
async def sync_export_endpoint(req: ExportRequest):
    """Trigger an incremental (or full) export to the sync directory."""
    try:
        async with _sync_semaphore:
            from sync.export import export_all
            from sync.manifest import read_manifest

            sync_dir = req.sync_dir or config.SYNC_DIR
            since = req.since
            # Auto-read last_exported_at for incremental default
            if since is None and not req.domains:
                try:
                    manifest = read_manifest(sync_dir)
                    since = manifest.get("last_exported_at")
                except (FileNotFoundError, ValueError):
                    pass

            result = export_all(
                driver=get_neo4j(),
                chroma_url=config.CHROMA_URL,
                redis_client=get_redis(),
                sync_dir=sync_dir,
                machine_id=req.machine_id or config.MACHINE_ID,
                since=since,
                domains=req.domains,
            )
        return result
    except Exception as exc:
        logger.error("Sync export failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sync/import")
async def sync_import_endpoint(req: ImportRequest):
    """Trigger a merge import from the sync directory."""
    try:
        async with _sync_semaphore:
            from sync.import_ import import_all

            result = import_all(
                driver=get_neo4j(),
                chroma_url=config.CHROMA_URL,
                redis_client=get_redis(),
                sync_dir=req.sync_dir or config.SYNC_DIR,
                force=req.force,
                conflict_strategy=req.conflict_strategy,
            )
        return result
    except Exception as exc:
        logger.error("Sync import failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/sync/status")
async def sync_status_endpoint(sync_dir: str | None = Query(default=None)):
    """Compare local DB counts against the sync directory manifest."""
    try:
        from sync.status import compare_status

        result = compare_status(
            driver=get_neo4j(),
            chroma_url=config.CHROMA_URL,
            redis_client=get_redis(),
            sync_dir=sync_dir or config.SYNC_DIR,
        )
        return result
    except Exception as exc:
        logger.error("Sync status failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
