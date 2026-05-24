# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""RSS/Atom SourceConnector — B2.2.

Wraps the stdlib XML parsing already in
``app.data_sources.rss_feed`` (legacy, Redis-backed) into the new
:class:`core.ingest.sources.base.SourceConnector` protocol so the
F1 / F2 / F3 wizard, the SSE source-activity stream, and the Source
node lifecycle can all drive RSS the same way they drive any other
connector kind.

Cursor shape: ``{"last_guid": str | None, "last_published_at": iso8601 | None}``

The legacy poller's per-entry dedup is via ``cerid:rss:seen`` Redis
set; for connector-protocol consumers we lean on the cursor instead
so resume is deterministic. (Both work fine in parallel — different
ingest paths, same artifact-id space.)
"""
from __future__ import annotations

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

logger = logging.getLogger("ai-companion.connectors.rss")

_USER_AGENT = "CeridAI-RSS/1.0"
_FETCH_TIMEOUT = 10.0


class RssConnector(SourceConnector):
    """Polls an RSS or Atom feed at a configurable cadence."""

    kind = "rss"
    tier = "core"

    async def connect(self, config: dict[str, Any]) -> ConnectResult:
        """Validate the feed URL and confirm it parses.

        Returns a ``ConnectResult`` with ``connection_time_ms`` set
        to the wall-clock time of the validation fetch. The initial
        cursor is empty — the first ``fetch_since`` call will walk
        the whole feed once and emit a cursor per entry.
        """
        url = (config.get("url") or "").strip()
        if not url:
            raise ValueError("config.url is required")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"unsupported url scheme: {parsed.scheme!r}")

        started = time.perf_counter()
        # One real fetch to confirm the feed is reachable + parses. Use
        # a small read; we just need the root element.
        try:
            async with httpx.AsyncClient(
                timeout=_FETCH_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": _USER_AGENT},
            ) as client:
                resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ValueError(f"feed fetch failed: {exc}") from exc

        # Cheap shape check — body must contain either <rss or <feed
        body_lower = resp.text[:4096].lower()
        if "<rss" not in body_lower and "<feed" not in body_lower:
            raise ValueError("response does not look like an RSS or Atom feed")

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        normalized = {
            "url": url,
            "name": config.get("name") or parsed.netloc,
            "poll_interval_seconds": int(config.get("poll_interval_seconds", 900)),
        }
        return ConnectResult(
            source_id=str(uuid.uuid4()),
            config=normalized,
            connection_time_ms=elapsed_ms,
            initial_cursor={},
        )

    async def fetch_since(
        self, source_id: str, cursor: dict[str, Any]
    ) -> AsyncIterator[SourceArtifactEvent]:
        """Yield :class:`SourceArtifactEvent` per new feed entry.

        Phase 2B scope: this is the stub that the Phase 2B follow-up
        worker (``app.workers.ingest_rss``) will exercise — for now
        it short-circuits to an empty iterator so connector
        registration and health-checks land cleanly. The actual fetch
        loop wires in alongside the worker.
        """
        # Phase 2B-follow-up: real walk. Empty iterator until then.
        if False:  # pragma: no cover - placeholder for type checker
            yield SourceArtifactEvent(  # type: ignore[unreachable]
                source_id=source_id,
                artifact_id="",
                elapsed_ms=0,
                cursor_after={},
            )
        return

    async def health_check(self, source_id: str, config: dict[str, Any]) -> HealthStatus:
        """Cheap HEAD/GET probe — confirms the feed is still reachable.

        ``config`` is passed in by the caller (the app layer owns the
        Neo4j round-trip) so this stays inside the core → app import
        contract.
        """
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
                # Some servers don't support HEAD; retry with GET
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
        """No-op — RSS is stateless, just a stored URL."""
        return
