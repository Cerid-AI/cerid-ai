# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Whisper model download manager (Phase E).

The meeting_capture plugin's transcribe.py loads ggml-format Whisper models
that pywhispercpp pulls from HuggingFace on first use. This router gives the
UI explicit control: list available models with sizes + RTF estimates, kick
off a download with progress, and report which are already cached.

Cache location: ~/.cerid/models/whisper/  (canonical, matches transcribe.py).
Source: huggingface.co/ggerganov/whisper.cpp  (official ggml conversions).

Downloads run as background tasks. State is held in process memory keyed by
download_id (UUID). Cancellation is cooperative: setting cancelled flips a
flag the streaming loop checks per-chunk.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import uuid
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.utils.swallowed import log_swallowed_error

_logger = logging.getLogger("ai-companion.whisper_models")

router = APIRouter(prefix="/settings/whisper", tags=["whisper-models"])


# ---------------------------------------------------------------------------
# Available models — canonical list with sizes + RTF estimates
# ---------------------------------------------------------------------------
#
# RTF (real-time factor) lower = faster.  Estimates from upstream whisper.cpp
# benchmarks averaged across CPU + Apple Silicon platforms; consumed by UI to
# guide the user toward an appropriate quality/speed tradeoff.

_MODEL_REGISTRY: dict[str, dict] = {
    "tiny": {
        "filename": "ggml-tiny.bin",
        "size_mb": 75,
        "rtf_cpu": 0.05,
        "rtf_apple_silicon": 0.02,
        "quality": "low",
        "description": "Fastest; good for noisy or short recordings.",
    },
    "base": {
        "filename": "ggml-base.bin",
        "size_mb": 142,
        "rtf_cpu": 0.10,
        "rtf_apple_silicon": 0.04,
        "quality": "low-medium",
        "description": "Best size/speed tradeoff; suitable for clear speech.",
    },
    "small": {
        "filename": "ggml-small.bin",
        "size_mb": 466,
        "rtf_cpu": 0.20,
        "rtf_apple_silicon": 0.08,
        "quality": "medium",
        "description": "Good general-purpose; handles accents reasonably.",
    },
    "medium": {
        "filename": "ggml-medium.bin",
        "size_mb": 1500,
        "rtf_cpu": 0.40,
        "rtf_apple_silicon": 0.18,
        "quality": "high",
        "description": "High accuracy; recommended default for meetings.",
    },
    "medium-q5_0": {
        "filename": "ggml-medium-q5_0.bin",
        "size_mb": 539,
        "rtf_cpu": 0.30,
        "rtf_apple_silicon": 0.15,
        "quality": "high",
        "description": "Quantized medium; ~⅓ the size with minimal accuracy loss.",
    },
    "large-v3": {
        "filename": "ggml-large-v3.bin",
        "size_mb": 3100,
        "rtf_cpu": 0.80,
        "rtf_apple_silicon": 0.35,
        "quality": "highest",
        "description": "Best accuracy; slowest. For archival-quality transcripts.",
    },
}

_HF_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"


def _is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def _cache_dir() -> Path:
    p = Path.home() / ".cerid" / "models" / "whisper"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class WhisperModelInfo(BaseModel):
    id: str
    filename: str
    size_mb: int
    rtf_estimate: float  # uses Apple Silicon estimate if applicable, else CPU
    quality: str
    description: str
    cached: bool
    cached_size_bytes: int | None = None


class WhisperModelListResponse(BaseModel):
    models: list[WhisperModelInfo]
    cache_dir: str
    current_default: str  # value of WHISPER_MODEL env


class DownloadStartRequest(BaseModel):
    model_id: str


class DownloadStartResponse(BaseModel):
    download_id: str
    model_id: str


class DownloadStatus(BaseModel):
    download_id: str
    model_id: str
    state: Literal["pending", "downloading", "completed", "failed", "cancelled"]
    bytes_downloaded: int
    bytes_total: int | None
    error: str | None = None


# ---------------------------------------------------------------------------
# In-memory download state (single-process, not multi-worker safe — acceptable
# for desktop/single-tenant deployment; multi-worker would back this with Redis)
# ---------------------------------------------------------------------------

_DOWNLOADS: dict[str, DownloadStatus] = {}
_CANCEL_FLAGS: dict[str, asyncio.Event] = {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/models", response_model=WhisperModelListResponse)
async def list_models() -> WhisperModelListResponse:
    """List available Whisper models with cached state."""
    cache_dir = _cache_dir()
    apple = _is_apple_silicon()
    items: list[WhisperModelInfo] = []
    for model_id, meta in _MODEL_REGISTRY.items():
        cached_path = cache_dir / meta["filename"]
        cached = cached_path.exists()
        items.append(
            WhisperModelInfo(
                id=model_id,
                filename=meta["filename"],
                size_mb=meta["size_mb"],
                rtf_estimate=meta["rtf_apple_silicon"] if apple else meta["rtf_cpu"],
                quality=meta["quality"],
                description=meta["description"],
                cached=cached,
                cached_size_bytes=cached_path.stat().st_size if cached else None,
            )
        )
    return WhisperModelListResponse(
        models=items,
        cache_dir=str(cache_dir),
        current_default=os.getenv("WHISPER_MODEL", "medium-q5_0"),
    )


async def _do_download(download_id: str, model_id: str) -> None:
    """Streaming download. Cooperatively cancels via _CANCEL_FLAGS event."""
    meta = _MODEL_REGISTRY[model_id]
    url = f"{_HF_BASE}/{meta['filename']}"
    dest = _cache_dir() / meta["filename"]
    tmp = dest.with_suffix(dest.suffix + ".partial")
    cancel = _CANCEL_FLAGS[download_id]
    status = _DOWNLOADS[download_id]

    try:
        status.state = "downloading"
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0)) as client:
            async with client.stream("GET", url, follow_redirects=True) as resp:
                if resp.status_code != 200:
                    status.state = "failed"
                    status.error = f"HTTP {resp.status_code}"
                    return
                total = int(resp.headers.get("content-length", 0)) or None
                status.bytes_total = total
                with tmp.open("wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        if cancel.is_set():
                            status.state = "cancelled"
                            f.close()
                            try:
                                tmp.unlink()
                            except OSError as exc:
                                log_swallowed_error(__name__, exc)
                            return
                        f.write(chunk)
                        status.bytes_downloaded += len(chunk)
        tmp.rename(dest)
        status.state = "completed"
    except (httpx.HTTPError, OSError) as exc:
        status.state = "failed"
        status.error = str(exc)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError as cleanup_exc:
            log_swallowed_error(__name__, cleanup_exc)


@router.post("/download", response_model=DownloadStartResponse)
async def start_download(req: DownloadStartRequest) -> DownloadStartResponse:
    """Kick off a download. Returns immediately with a download_id to poll."""
    if req.model_id not in _MODEL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown model: {req.model_id}")
    download_id = uuid.uuid4().hex
    _DOWNLOADS[download_id] = DownloadStatus(
        download_id=download_id,
        model_id=req.model_id,
        state="pending",
        bytes_downloaded=0,
        bytes_total=None,
    )
    _CANCEL_FLAGS[download_id] = asyncio.Event()
    asyncio.create_task(_do_download(download_id, req.model_id))
    return DownloadStartResponse(download_id=download_id, model_id=req.model_id)


@router.get("/download/{download_id}", response_model=DownloadStatus)
async def get_download_status(download_id: str) -> DownloadStatus:
    status = _DOWNLOADS.get(download_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Unknown download_id")
    return status


@router.delete("/download/{download_id}", response_model=DownloadStatus)
async def cancel_download(download_id: str) -> DownloadStatus:
    """Cooperative cancel. Sets the flag the download loop polls; the next
    chunk write will flip state to 'cancelled' and remove the partial file."""
    status = _DOWNLOADS.get(download_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Unknown download_id")
    cancel = _CANCEL_FLAGS.get(download_id)
    if cancel is not None:
        cancel.set()
    return status


@router.delete("/models/{model_id}")
async def delete_cached_model(model_id: str) -> dict[str, bool]:
    """Remove a cached model file."""
    if model_id not in _MODEL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown model: {model_id}")
    path = _cache_dir() / _MODEL_REGISTRY[model_id]["filename"]
    if path.exists():
        path.unlink()
        return {"deleted": True}
    return {"deleted": False}
