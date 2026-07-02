# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""App-version update check — GET /updates/check.

Compares the running version against the latest GitHub release.
Best-effort: any fetch failure / timeout / rate-limit returns a degraded
``{running, latest: null, update_available: false, error: "..."}`` — never 500.
"""
from __future__ import annotations

import asyncio
import logging
import time
from http import HTTPStatus
from typing import Any

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel

from core.utils.swallowed import log_swallowed_error
from core.utils.version import get_version

logger = logging.getLogger("ai-companion.updates")

router = APIRouter(prefix="/updates", tags=["updates"])

_GITHUB_RELEASES_URL = (
    "https://api.github.com/repos/Cerid-AI/cerid-ai/releases/latest"
)
_FETCH_TIMEOUT = 5.0  # seconds — short, best-effort

# Simple 1-hour in-memory cache to avoid hammering the GitHub rate limit.
_cache_lock = asyncio.Lock()
_cache: dict[str, Any] = {"result": None, "expires_at": 0.0}
_CACHE_TTL = 3600.0  # seconds


class UpdateCheckResponse(BaseModel):
    running: str
    latest: str | None
    update_available: bool
    release_url: str | None = None
    error: str | None = None


def _semver_gt(a: str, b: str) -> bool:
    """Return True if version string ``a`` is strictly greater than ``b``.

    Uses ``packaging.version`` when available; falls back to tuple comparison
    of numeric components so as not to hard-require the package.
    """
    def _parts(v: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in v.split(".") if x.isdigit())
        except Exception:  # noqa: BLE001  # silent-catch-allowed: malformed version string → (0,)
            return (0,)

    try:
        from packaging.version import Version  # type: ignore[import-untyped]
        return Version(a) > Version(b)
    except Exception:  # noqa: BLE001  # silent-catch-allowed: packaging missing/parse error → tuple fallback
        return _parts(a) > _parts(b)


async def _fetch_latest_release() -> dict[str, str] | None:
    """Fetch the latest release from GitHub.

    Returns ``{"latest": "<version>", "release_url": "<url>"}`` on success,
    or ``None`` when the response cannot be parsed / status is not 200.

    Callers must handle exceptions (network errors, timeouts) themselves.
    """
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
        resp = await client.get(
            _GITHUB_RELEASES_URL,
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
            follow_redirects=True,
        )

    if resp.status_code != HTTPStatus.OK:
        logger.debug("GitHub releases API returned %d", resp.status_code)
        return None

    data = resp.json()
    tag_name: str = data.get("tag_name", "")
    html_url: str = data.get("html_url", "")
    if not tag_name:
        return None

    # Strip leading 'v' — releases are tagged as "v1.2.3"
    version = tag_name.lstrip("v")
    return {"latest": version, "release_url": html_url}


async def _cached_fetch_latest_release() -> dict[str, str] | None:
    """Return cached release info, refreshing when the TTL has expired."""
    now = time.monotonic()
    async with _cache_lock:
        if now < _cache["expires_at"]:
            return _cache["result"]  # type: ignore[return-value]
        result = await _fetch_latest_release()
        _cache["result"] = result
        _cache["expires_at"] = now + _CACHE_TTL
        return result


async def _get_release(force: bool) -> dict[str, str] | None:
    """Return release info, bypassing and refreshing the cache when ``force`` is True."""
    if not force:
        return await _cached_fetch_latest_release()
    # Force path: fetch fresh, then update cache so subsequent non-forced calls see it.
    result = await _fetch_latest_release()
    now = time.monotonic()
    async with _cache_lock:
        _cache["result"] = result
        _cache["expires_at"] = now + _CACHE_TTL
    return result


@router.get(
    "/check",
    response_model=UpdateCheckResponse,
    summary="Check for app updates",
    description=(
        "Use when: you want to know whether a newer version of Cerid AI is available. "
        "Returns: running version, latest published release, whether an update is available, "
        "and a release URL. Any fetch failure returns update_available=false with an error field — never 500."
    ),
)
async def check_for_updates(
    force: bool = Query(False, description="Bypass the 1-hour cache and fetch a live result from GitHub."),
) -> UpdateCheckResponse:
    """Compare the running version against the latest GitHub release."""
    running = get_version()

    try:
        release = await _get_release(force)
    except Exception as exc:
        log_swallowed_error(__name__, exc)
        logger.debug("Update check fetch failed: %s", exc)
        return UpdateCheckResponse(
            running=running,
            latest=None,
            update_available=False,
            error=f"Could not reach update server: {type(exc).__name__}",
        )

    if release is None:
        return UpdateCheckResponse(
            running=running,
            latest=None,
            update_available=False,
            error="Could not retrieve release information",
        )

    latest = release["latest"]
    try:
        update_available = _semver_gt(latest, running)
    except Exception as exc:
        log_swallowed_error(__name__, exc)
        update_available = False

    return UpdateCheckResponse(
        running=running,
        latest=latest,
        update_available=update_available,
        release_url=release.get("release_url") if update_available else None,
    )
