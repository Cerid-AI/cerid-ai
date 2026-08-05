# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""URL-watch SourceConnector.

Polls a single URL on a cadence and emits a SourceArtifactEvent when the
content hash changes. Use cases: docs pages with version history, status
pages, regulatory PDF endpoints.

Cursor shape: ``{"last_hash": str | None, "last_checked_at": iso8601 | None}``

All fetches route through the shared SSRF guard (``safe_fetch.guarded_get``):
the URL is operator-supplied, so an internal target must be refused on every
fetch site — connect(), fetch_since(), and health_check().
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from http import HTTPStatus
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import httpx

from core.ingest.sources.base import (
    ConnectResult,
    HealthStatus,
    SourceArtifactEvent,
    SourceConnector,
)
from core.ingest.sources.ingest_sink import get_source_ingest_fn
from core.ingest.sources.safe_fetch import DEFAULT_MAX_BYTES, guarded_get
from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.connectors.url_watch")

_USER_AGENT = "CeridAI-UrlWatch/1.0"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


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
            resp = await guarded_get(url, user_agent=_USER_AGENT)  # SSRF-guarded
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ValueError(f"url fetch failed: {exc}") from exc
        # guarded_get raises ValueError on a blocked/internal target; that
        # propagates as the connect() validation error, rejecting the source.

        initial_hash = _content_hash(resp.text[:DEFAULT_MAX_BYTES])
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
        self, source_id: str, cursor: dict[str, Any], config: dict[str, Any]
    ) -> AsyncIterator[SourceArtifactEvent]:
        """Yield exactly one event if the content hash changed since the cursor,
        otherwise yield nothing. Driven by the polling worker on the configured
        cadence; new content is ingested via the DI ingest sink.
        """
        ingest_fn = get_source_ingest_fn()
        url = (config or {}).get("url", "")
        if ingest_fn is None or not url:
            return

        try:
            resp = await guarded_get(url, user_agent=_USER_AGENT)  # SSRF-guarded
            resp.raise_for_status()
        except (ValueError, httpx.HTTPError) as exc:
            # ValueError = blocked/internal target (SSRF guard); HTTPError =
            # network failure. Skip gracefully rather than crash the poll sweep —
            # the cursor doesn't advance, so we retry next poll.
            logger.warning("url_watch.fetch_since: fetch blocked/failed for %s: %s", source_id, exc)
            return

        body = resp.text[:DEFAULT_MAX_BYTES]
        new_hash = _content_hash(body)
        prev_hash = (cursor or {}).get("last_hash")
        if new_hash == prev_hash:
            return  # unchanged — nothing to ingest, cursor stays put

        content = body.strip()
        if not content:
            return

        domain = (config.get("domain") or "general").strip() or "general"
        parsed = urlparse(url)
        title = config.get("name") or parsed.netloc + parsed.path
        t0 = time.monotonic()
        try:
            artifact_id = await ingest_fn(
                content,
                domain=domain,
                metadata={
                    "source_id": source_id,
                    "source_type": "url_watch",
                    "title": title,
                    "url": url,
                },
            )
        except Exception as exc:  # noqa: BLE001 — a bad ingest must not abort the source
            log_swallowed_error("core.ingest.sources.connectors.url_watch.fetch_since", exc)
            # Don't advance the cursor — retried next poll (at-least-once;
            # ingest_content dedups re-delivery by content hash).
            return

        yield SourceArtifactEvent(
            source_id=source_id,
            artifact_id=str(artifact_id or ""),
            elapsed_ms=int((time.monotonic() - t0) * 1000),
            cursor_after={"last_hash": new_hash, "last_checked_at": utcnow_iso()},
            title=title,
            domain=domain,
        )

    async def health_check(self, source_id: str, config: dict[str, Any]) -> HealthStatus:
        url = (config or {}).get("url", "")
        if not url:
            return HealthStatus(ok=False, detail="source has no url")

        try:
            resp = await guarded_get(url, method="HEAD", user_agent=_USER_AGENT)  # SSRF-guarded
            if resp.status_code >= HTTPStatus.BAD_REQUEST:
                resp = await guarded_get(url, user_agent=_USER_AGENT)  # some servers reject HEAD
            if resp.status_code >= HTTPStatus.BAD_REQUEST:
                return HealthStatus(
                    ok=False,
                    detail=f"HTTP {resp.status_code}",
                    last_error=resp.text[:200],
                )
            return HealthStatus(ok=True, detail=f"HTTP {resp.status_code}")
        except ValueError as exc:
            return HealthStatus(ok=False, detail="blocked target", last_error=str(exc))
        except httpx.HTTPError as exc:
            return HealthStatus(ok=False, detail="network error", last_error=str(exc))

    async def disconnect(self, source_id: str, config: dict[str, Any]) -> None:
        return
