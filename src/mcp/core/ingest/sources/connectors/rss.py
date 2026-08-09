# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""RSS/Atom SourceConnector.

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
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
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
from core.ingest.sources.safe_fetch import guarded_get
from core.utils.safe_xml import safe_fromstring

logger = logging.getLogger("ai-companion.connectors.rss")

_USER_AGENT = "CeridAI-RSS/1.0"
_MAX_FEED_BYTES = 8 * 1024 * 1024  # cap untrusted feed body (memory / DoS guard)
_ATOM = "{http://www.w3.org/2005/Atom}"
_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"
def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def _normalize_date(raw: str) -> str | None:
    """Best-effort ISO-8601 from an RSS RFC-822 or Atom ISO date string."""
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).isoformat()  # RSS <pubDate>
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()  # Atom
    except (TypeError, ValueError):
        return raw  # keep raw if unparseable — still usable as an ordering hint


def _parse_feed(
    xml_text: str, cursor: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse an RSS or Atom feed into NEW entries (oldest-first) + advanced cursor.

    Pure + deterministic (no network) so the parsing — the risky part — is unit
    tested against crafted samples. Feeds are newest-first on the wire; we walk
    from the top collecting entries until we reach ``cursor['last_guid']`` (the
    last one already ingested), then reverse so the caller ingests oldest→newest
    and the cursor advances monotonically. Malformed XML → ``([], cursor)``.
    """
    cursor = cursor or {}
    last_guid = cursor.get("last_guid")
    # XXE / billion-laughs hardening (feeds are untrusted external input).
    # Primary guard is the shared defusedxml wrapper (safe_fromstring), which
    # forbids DTDs/entities. The DOCTYPE/ENTITY string-scan below is kept as a
    # cheap fast-reject so obviously-malicious feeds never reach the parser.
    lowered = xml_text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        logger.warning(
            "rss._parse_feed: refusing feed with DOCTYPE/ENTITY declaration "
            "(XXE / entity-expansion guard)",
        )
        return [], cursor
    try:
        root = safe_fromstring(xml_text)
    except ET.ParseError:
        return [], cursor

    items: list[dict[str, Any]] = []
    # RSS 2.0: <item>
    for it in root.iter("item"):
        guid = _text(it.find("guid")) or _text(it.find("link"))
        if not guid:
            continue
        items.append({
            "guid": guid,
            "title": _text(it.find("title")),
            "url": _text(it.find("link")),
            "content": _text(it.find("description")) or _text(it.find(_CONTENT_NS)),
            "published_at": _normalize_date(_text(it.find("pubDate"))),
        })
    # Atom: <entry>
    for en in root.iter(f"{_ATOM}entry"):
        link_el = en.find(f"{_ATOM}link")
        url = link_el.get("href", "") if link_el is not None else ""
        guid = _text(en.find(f"{_ATOM}id")) or url or _text(en.find(f"{_ATOM}title"))
        if not guid:
            continue
        items.append({
            "guid": guid,
            "title": _text(en.find(f"{_ATOM}title")),
            "url": url,
            "content": _text(en.find(f"{_ATOM}content")) or _text(en.find(f"{_ATOM}summary")),
            "published_at": _normalize_date(
                _text(en.find(f"{_ATOM}published")) or _text(en.find(f"{_ATOM}updated"))
            ),
        })

    if not items:
        return [], cursor

    new_items: list[dict[str, Any]] = []
    for parsed in items:  # feed order = newest-first
        if last_guid is not None and parsed["guid"] == last_guid:
            break
        new_items.append(parsed)
    if not new_items:
        return [], cursor

    newest = items[0]
    new_cursor = {
        "last_guid": newest["guid"],
        "last_published_at": newest.get("published_at"),
    }
    new_items.reverse()  # oldest-first for monotonic cursor advance
    return new_items, new_cursor


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
            resp = await guarded_get(url, user_agent=_USER_AGENT)  # SSRF-guarded (scheme + non-internal + no auto-redirect)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ValueError(f"feed fetch failed: {exc}") from exc
        # _guarded_get raises ValueError on a blocked/internal target — that
        # propagates as the connect() validation error (same type), rejecting
        # the source at creation time.

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
        self, source_id: str, cursor: dict[str, Any], config: dict[str, Any]
    ) -> AsyncIterator[SourceArtifactEvent]:
        """Fetch the feed, ingest each entry newer than ``cursor``, and yield one
        :class:`SourceArtifactEvent` per ingested artifact with the cursor
        advance embedded (``cursor_after``) so the polling worker persists it
        incrementally — crash-safe resume.

        Yields nothing (empty iterator) when the ingest sink isn't wired or the
        source has no ``url``, keeping registration + health-checks independent
        of the worker. ``config`` carries the source's ``url`` / ``domain`` (the
        app layer owns the Neo4j round-trip — same contract as health_check).
        """
        from core.ingest.sources.ingest_sink import get_source_ingest_fn
        from core.utils.swallowed import log_swallowed_error

        ingest_fn = get_source_ingest_fn()
        url = (config.get("url") or "").strip()
        if ingest_fn is None or not url:
            return

        try:
            resp = await guarded_get(url, user_agent=_USER_AGENT)  # SSRF-guarded fetch
            resp.raise_for_status()
            body = resp.text[:_MAX_FEED_BYTES]
        except (httpx.HTTPError, ValueError) as exc:
            # ValueError = blocked/internal target (SSRF guard) → skip this source
            # gracefully rather than crash the poll sweep.
            logger.warning("rss.fetch_since: feed fetch blocked/failed for %s: %s", source_id, exc)
            return

        entries, _new_cursor = _parse_feed(body, cursor)
        domain = (config.get("domain") or "general").strip() or "general"

        for entry in entries:  # oldest-first → monotonic cursor advance
            content = (entry.get("content") or entry.get("title") or "").strip()
            if not content:
                continue
            t0 = time.monotonic()
            try:
                artifact_id = await ingest_fn(
                    content,
                    domain=domain,
                    metadata={
                        "source_id": source_id,
                        "source_type": "rss",
                        "title": entry.get("title", ""),
                        "url": entry.get("url", ""),
                        "guid": entry.get("guid", ""),
                        "published_at": entry.get("published_at") or "",
                    },
                )
            except Exception as exc:  # noqa: BLE001 — one bad entry must not abort the source
                log_swallowed_error("core.ingest.sources.connectors.rss.fetch_since", exc)
                # Stop here WITHOUT advancing past this entry — the cursor sits at
                # the last successfully-yielded event, so this one is retried next
                # poll (at-least-once; ingest_content dedups re-delivery).
                return
            yield SourceArtifactEvent(
                source_id=source_id,
                artifact_id=str(artifact_id or ""),
                elapsed_ms=int((time.monotonic() - t0) * 1000),
                cursor_after={
                    "last_guid": entry["guid"],
                    "last_published_at": entry.get("published_at"),
                },
                title=entry.get("title", ""),
                domain=domain,
            )

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
            resp = await guarded_get(url, method="HEAD", user_agent=_USER_AGENT)  # SSRF-guarded probe
            if resp.status_code >= HTTPStatus.BAD_REQUEST:
                # Some servers don't support HEAD; retry with GET
                resp = await guarded_get(url, user_agent=_USER_AGENT)
            if resp.status_code >= HTTPStatus.BAD_REQUEST:
                return HealthStatus(
                    ok=False,
                    detail=f"HTTP {resp.status_code}",
                    last_error=resp.text[:200],
                )
            return HealthStatus(ok=True, detail=f"HTTP {resp.status_code}")
        except ValueError as exc:
            # Blocked/internal target (SSRF guard) — surface as an unhealthy probe.
            return HealthStatus(ok=False, detail="blocked target", last_error=str(exc))
        except httpx.HTTPError as exc:
            return HealthStatus(ok=False, detail="network error", last_error=str(exc))

    async def disconnect(self, source_id: str, config: dict[str, Any]) -> None:
        """No-op — RSS is stateless, just a stored URL."""
        return
