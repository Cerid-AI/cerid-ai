# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Bookmarks one-shot importer.

Accepts a NETSCAPE-formatted HTML export (the universal interchange
format produced by Chrome, Firefox, Safari, Edge, Brave, etc.).
Parses out (title, url) pairs and stores them in the (:Source) node's
config under ``parsed_bookmarks`` for the ingest worker to consume.

Cursor shape: ``{"imported": bool}`` — flipped to ``true`` once the
worker has finished ingesting all parsed bookmarks. One-shot source:
no fetch_since cadence; the source's lifetime is the import + a
provenance record.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from html import unescape
from typing import Any, AsyncIterator

from core.ingest.sources.base import (
    ConnectResult,
    HealthStatus,
    SourceArtifactEvent,
    SourceConnector,
)

logger = logging.getLogger("ai-companion.connectors.bookmarks")

# NETSCAPE bookmark format — each entry is:
#   <DT><A HREF="<url>" ADD_DATE="..." [other attrs]>title</A>
# Folders use <DT><H3> ... </H3><DL><p> nested entries </DL><p>
_BOOKMARK_RE = re.compile(
    r'<DT>\s*<A\s+[^>]*HREF=["\']([^"\']+)["\'][^>]*>(.*?)</A>',
    re.IGNORECASE | re.DOTALL,
)

# Cap on bookmarks per import. The wizard surfaces the count; users
# with larger libraries can chunk their exports.
_MAX_BOOKMARKS = 50_000


class BookmarksConnector(SourceConnector):
    """One-shot importer for NETSCAPE HTML bookmark exports."""

    kind = "bookmarks"
    tier = "core"

    async def connect(self, config: dict[str, Any]) -> ConnectResult:
        """Parse the supplied HTML and store the bookmark list.

        Accepted config:

        * ``html`` (required) — full HTML export as a string. The
          wizard uploads the file in the browser and posts its
          contents inline; servers stay file-system-free.
        """
        html = config.get("html")
        if not isinstance(html, str) or not html.strip():
            raise ValueError("config.html (string) is required")

        started = time.perf_counter()
        matches = _BOOKMARK_RE.findall(html)
        bookmarks = []
        for url, title_raw in matches[:_MAX_BOOKMARKS]:
            url = url.strip()
            if not url:
                continue
            # Strip nested HTML inside the title text (Firefox occasionally
            # wraps it in <FONT> etc.) before unescaping entities.
            title = unescape(re.sub(r"<[^>]+>", " ", title_raw)).strip()
            bookmarks.append({"url": url, "title": title or url})

        if not bookmarks:
            raise ValueError("no bookmarks found in HTML — is this a NETSCAPE export?")

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        normalized = {
            "name": config.get("name") or f"Bookmarks ({len(bookmarks)})",
            "parsed_bookmarks": bookmarks,
            "parsed_count": len(bookmarks),
            "truncated": len(matches) > _MAX_BOOKMARKS,
        }
        return ConnectResult(
            source_id=str(uuid.uuid4()),
            config=normalized,
            connection_time_ms=elapsed_ms,
            initial_cursor={"imported": False},
        )

    async def fetch_since(
        self, source_id: str, cursor: dict[str, Any], config: dict[str, Any]
    ) -> AsyncIterator[SourceArtifactEvent]:
        """One-shot import — no cadenced fetch. The ingest worker
        reads ``config.parsed_bookmarks``, emits artifacts once, and
        flips ``cursor.imported=True`` so we never re-import."""
        if False:  # pragma: no cover
            yield SourceArtifactEvent(  # type: ignore[unreachable]
                source_id=source_id,
                artifact_id="",
                elapsed_ms=0,
                cursor_after={},
            )
        return

    async def health_check(self, source_id: str, config: dict[str, Any]) -> HealthStatus:
        """Reports parsed count and whether the import has completed."""
        count = (config or {}).get("parsed_count", 0)
        return HealthStatus(
            ok=True,
            detail=f"{count} bookmarks parsed",
        )

    async def disconnect(self, source_id: str, config: dict[str, Any]) -> None:
        return
