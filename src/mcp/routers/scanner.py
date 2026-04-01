# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Folder Scanner REST API — start, monitor, and manage autonomous folder scans.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import config
from config.constants import MIN_QUALITY_SCORE
from deps import get_redis
from errors import IngestionError
from services.folder_scanner import (
    clear_scan_state,
    get_scan_state,
    preview_folder,
    scan_folder,
)

logger = logging.getLogger("ai-companion.scanner")

router = APIRouter(tags=["scanner"])


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class ScanRequest(BaseModel):
    path: str
    min_quality: float = MIN_QUALITY_SCORE
    max_file_size_mb: int = 50
    dry_run: bool = False
    exclude_patterns: list[str] = Field(default_factory=list)


class ScanProgress(BaseModel):
    scan_id: str
    status: str  # "started" | "running" | "complete" | "error"
    files_scanned: int = 0
    files_total_estimate: int = 0
    files_ingested: int = 0
    files_skipped: int = 0
    files_errored: int = 0
    started_at: str = ""
    elapsed_s: float = 0.0
    results: list[dict] | None = None


class ScanState(BaseModel):
    files_tracked: int = 0
    last_scan_at: str | None = None
    total_ingested: int = 0
    total_skipped: int = 0
    total_errored: int = 0


class PreviewRequest(BaseModel):
    path: str
    max_file_size_mb: int = 50

class PreviewResponse(BaseModel):
    total_files: int = 0
    total_scanned: int = 0
    total_size_mb: float = 0.0
    by_extension: dict = Field(default_factory=dict)
    by_domain: dict = Field(default_factory=dict)
    estimated_chunks: int = 0
    estimated_storage_mb: float = 0.0
    skipped: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# In-memory scan tracking
# ---------------------------------------------------------------------------

_active_scans: dict[str, ScanProgress] = {}
_scan_flags: dict[str, dict[str, bool]] = {}  # scan_id → {"paused": bool, "cancelled": bool}


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

async def _run_scan(scan_id: str, req: ScanRequest) -> None:
    """Background task that drives the scan_folder async generator."""
    progress = _active_scans[scan_id]
    progress.status = "running"
    flags = _scan_flags.setdefault(scan_id, {"paused": False, "cancelled": False})
    results: list[dict] = []

    try:
        async for result in scan_folder(
            req.path,
            min_quality=req.min_quality,
            max_file_size_mb=req.max_file_size_mb,
            exclude_patterns=set(req.exclude_patterns) if req.exclude_patterns else None,
            dry_run=req.dry_run,
        ):
            # Check cancel
            if flags["cancelled"]:
                progress.status = "cancelled"
                break

            # Check pause — wait until resumed
            while flags["paused"] and not flags["cancelled"]:
                progress.status = "paused"
                await asyncio.sleep(0.5)
            if flags["cancelled"]:
                progress.status = "cancelled"
                break
            progress.status = "running"

            progress.files_scanned += 1
            progress.elapsed_s = round(time.time() - _scan_start_times[scan_id], 2)

            if result.status == "ingested":
                progress.files_ingested += 1
            elif result.status in ("duplicate", "low_quality", "skipped", "unsupported"):
                progress.files_skipped += 1
            elif result.status == "error":
                progress.files_errored += 1

            results.append(asdict(result))

        if progress.status != "cancelled":
            progress.status = "complete"
        progress.results = results
        progress.elapsed_s = round(time.time() - _scan_start_times[scan_id], 2)
        logger.info(
            f"Scan {scan_id} {progress.status}: {progress.files_ingested} ingested, "
            f"{progress.files_skipped} skipped, {progress.files_errored} errored "
            f"({progress.elapsed_s:.1f}s)"
        )
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        progress.status = "error"
        progress.elapsed_s = round(time.time() - _scan_start_times[scan_id], 2)
        logger.error(f"Scan {scan_id} failed: {e}")


_scan_start_times: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _validate_scan_path(path: str) -> None:
    """Ensure path exists and is within configured SCAN_PATHS."""
    if not os.path.isdir(path):
        raise HTTPException(status_code=400, detail=f"Path does not exist or is not a directory: {path}")

    allowed = config.SCAN_PATHS.split(":") if hasattr(config, "SCAN_PATHS") else [config.ARCHIVE_PATH]
    real_path = os.path.realpath(path)
    for allowed_path in allowed:
        if real_path.startswith(os.path.realpath(allowed_path)):
            return
    raise HTTPException(status_code=403, detail=f"Path not within allowed SCAN_PATHS: {path}")


@router.post("/admin/scan")
async def start_scan(req: ScanRequest) -> dict:
    """Start an asynchronous folder scan."""
    _validate_scan_path(req.path)

    scan_id = str(uuid.uuid4())
    now = time.time()
    _scan_start_times[scan_id] = now

    from pathlib import Path

    from config.taxonomy import SUPPORTED_EXTENSIONS
    from services.folder_scanner import DEFAULT_EXCLUDE_DIRS, _estimate_file_count

    exclude = DEFAULT_EXCLUDE_DIRS | set(req.exclude_patterns or [])
    estimate = _estimate_file_count(Path(req.path), SUPPORTED_EXTENSIONS, exclude)

    progress = ScanProgress(
        scan_id=scan_id,
        status="started",
        files_total_estimate=estimate,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    )
    _active_scans[scan_id] = progress

    asyncio.create_task(_run_scan(scan_id, req))

    return {"scan_id": scan_id, "status": "started", "files_estimate": estimate}


@router.get("/admin/scan/state")
def scan_state() -> ScanState:
    """Return persistent scan state from Redis."""
    redis = get_redis()
    state = get_scan_state(redis)
    return ScanState(
        files_tracked=state["total_ingested"] + state["total_skipped"] + state["total_errored"],
        last_scan_at=state["last_scan_at"],
        total_ingested=state["total_ingested"],
        total_skipped=state["total_skipped"],
        total_errored=state["total_errored"],
    )


@router.get("/admin/scan/preview")
async def scan_preview_get(
    path: str = Query(..., description="Directory to preview"),
    max_file_size_mb: int = Query(50, description="Max file size in MB"),
) -> PreviewResponse:
    """Quick preview of a directory without ingesting (GET)."""
    _validate_scan_path(path)
    result = await preview_folder(path, max_file_size_mb=max_file_size_mb)
    return PreviewResponse(**result)


@router.post("/admin/scan/preview")
async def scan_preview_post(req: PreviewRequest) -> PreviewResponse:
    """Quick preview of a directory without ingesting (POST)."""
    _validate_scan_path(req.path)
    result = await preview_folder(req.path, max_file_size_mb=req.max_file_size_mb)
    return PreviewResponse(**result)


@router.get("/admin/scan/{scan_id}")
def get_scan_progress(scan_id: str) -> ScanProgress:
    """Get progress of an active or completed scan."""
    if scan_id not in _active_scans:
        raise HTTPException(status_code=404, detail=f"Scan not found: {scan_id}")
    return _active_scans[scan_id]


@router.get("/admin/scan/{scan_id}/stream")
async def stream_scan_progress(scan_id: str) -> StreamingResponse:
    """SSE stream of scan progress — real-time updates without polling."""
    if scan_id not in _active_scans:
        raise HTTPException(status_code=404, detail=f"Scan not found: {scan_id}")

    async def _event_generator():
        import json as _json

        last_scanned = 0
        while True:
            progress = _active_scans.get(scan_id)
            if not progress:
                break

            # Only emit when something changed
            if progress.files_scanned != last_scanned or progress.status in ("complete", "cancelled", "error"):
                last_scanned = progress.files_scanned
                # Estimate ETA
                eta_s = 0.0
                if progress.files_scanned > 0 and progress.files_total_estimate > 0:
                    rate = progress.files_scanned / max(progress.elapsed_s, 0.1)
                    remaining = progress.files_total_estimate - progress.files_scanned
                    eta_s = round(remaining / rate, 1) if rate > 0 else 0.0

                event = {
                    "type": "progress",
                    "status": progress.status,
                    "files_scanned": progress.files_scanned,
                    "files_total": progress.files_total_estimate,
                    "files_ingested": progress.files_ingested,
                    "files_skipped": progress.files_skipped,
                    "files_errored": progress.files_errored,
                    "elapsed_s": progress.elapsed_s,
                    "eta_s": eta_s,
                }
                yield f"data: {_json.dumps(event)}\n\n"

            if progress.status in ("complete", "cancelled", "error"):
                yield f"data: {_json.dumps({'type': 'complete', 'status': progress.status})}\n\n"
                break

            await asyncio.sleep(1)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/admin/scan/{scan_id}/pause")
def pause_scan(scan_id: str) -> dict:
    """Pause an active scan."""
    if scan_id not in _active_scans:
        raise HTTPException(status_code=404, detail=f"Scan not found: {scan_id}")
    flags = _scan_flags.setdefault(scan_id, {"paused": False, "cancelled": False})
    flags["paused"] = True
    return {"scan_id": scan_id, "status": "paused"}


@router.post("/admin/scan/{scan_id}/resume")
def resume_scan(scan_id: str) -> dict:
    """Resume a paused scan."""
    if scan_id not in _active_scans:
        raise HTTPException(status_code=404, detail=f"Scan not found: {scan_id}")
    flags = _scan_flags.setdefault(scan_id, {"paused": False, "cancelled": False})
    flags["paused"] = False
    return {"scan_id": scan_id, "status": "resumed"}


@router.post("/admin/scan/{scan_id}/cancel")
def cancel_scan(scan_id: str) -> dict:
    """Cancel an active scan."""
    if scan_id not in _active_scans:
        raise HTTPException(status_code=404, detail=f"Scan not found: {scan_id}")
    flags = _scan_flags.setdefault(scan_id, {"paused": False, "cancelled": False})
    flags["cancelled"] = True
    return {"scan_id": scan_id, "status": "cancelled"}


@router.post("/admin/scan/reset")
def reset_scan_state() -> dict:
    """Clear all persistent scan state from Redis."""
    redis = get_redis()
    cleared = clear_scan_state(redis)
    logger.info(f"Scan state cleared: {cleared} keys deleted")
    return {"cleared": cleared}
