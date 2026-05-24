# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""URL-watch SourceConnector — B2.5.

Polls a single URL on a cadence and emits a SourceArtifactEvent when
the content hash changes. Use cases: docs pages with version history,
status pages, regulatory PDF endpoints.

Cursor shape: ``{"last_hash": str | None, "last_checked_at": iso8601 | None}``
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import httpx

from core.ingest.sources.base import (
    ConnectResult,
    HealthStatus,
    SourceArtifactEvent,
    SourceConnector,
)

logger = logging.getLogger("ai-companion.connectors.url_watch")

_USER_AGENT = "CeridAI-UrlWatch/1.0"
_FETCH_TIMEOUT = 10.0


class UrlWatchConnector(SourceConnector):
    """Watches a single URL for content changes."""

    kind = "url_watch"
    tier = "core"

    async def connect(self, config: dict[str, Any]) -> ConnectResult:
        url = (config.get("url") or "").strip()
        if not url:
            raise ValueError("config.url is required")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"unsupported url scheme: {parsed.scheme!r}")

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=_FETCH_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": _USER_AGENT},
            ) as client:
                resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ValueError(f"url fetch failed: {exc}") from exc

        initial_hash = hashlib.sha256(resp.content).hexdigest()
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        normalized = {
            "url": url,
            "name": config.get("name") or parsed.netloc + parsed.path,
            "poll_interval_seconds": int(config.get("poll_interval_seconds", 3600)),
        }
        return ConnectResult(
            source_id=str(uuid.uuid4()),
            config=normalized,
            connection_time_ms=elapsed_ms,
            initial_cursor={"last_hash": initial_hash, "last_checked_at": None},
        )

    async def fetch_since(
        self, source_id: str, cursor: dict[str, Any]
    ) -> AsyncIterator[SourceArtifactEvent]:
        """Yield exactly one event if the content hash has changed
        since the cursor, otherwise yield nothing. The Phase 2B
        follow-up worker drives this on the cadence."""
        # Phase 2B-follow-up: real walk via the worker. Empty iterator
        # until then so connector registration + health stays green.
        if False:  # pragma: no cover
            yield SourceArtifactEvent(  # type: ignore[unreachable]
                source_id=source_id,
                artifact_id="",
                elapsed_ms=0,
                cursor_after={},
            )
        return

    async def health_check(self, source_id: str, config: dict[str, Any]) -> HealthStatus:
        url = (config or {}).get("url", "")
        if not url:
            return HealthStatus(ok=False, detail="source has no url")

        try:
            async with httpx.AsyncClient(
                timeout=_FETCH_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": _USER_AGENT},
            ) as client:
                resp = await client.head(url)
            if resp.status_code >= 400:
                async with httpx.AsyncClient(
                    timeout=_FETCH_TIMEOUT,
                    follow_redirects=True,
                    headers={"User-Agent": _USER_AGENT},
                ) as client:
                    resp = await client.get(url)
            if resp.status_code >= 400:
                return HealthStatus(
                    ok=False,
                    detail=f"HTTP {resp.status_code}",
                    last_error=resp.text[:200],
                )
            return HealthStatus(ok=True, detail=f"HTTP {resp.status_code}")
        except httpx.HTTPError as exc:
            return HealthStatus(ok=False, detail="network error", last_error=str(exc))

    async def disconnect(self, source_id: str, config: dict[str, Any]) -> None:
        return
