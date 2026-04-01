# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Data source management — list, enable/disable preloaded and custom sources."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from pydantic import BaseModel

from utils.error_handler import handle_errors

router = APIRouter(tags=["data-sources"])
logger = logging.getLogger("ai-companion.data_sources")


@router.get("/data-sources")
async def list_data_sources():
    """List all registered data sources with their status."""
    from utils.data_sources import registry
    sources = registry.list_sources()
    return {"sources": sources, "total": len(sources)}


@router.post("/data-sources/{name}/enable")
async def enable_source(name: str):
    """Enable a registered data source by name."""
    from utils.data_sources import registry
    for s in registry._sources.values():
        if s.name == name:
            s.enabled = True
            return {"status": "enabled", "name": name}
    return {"error": f"Source '{name}' not found"}


@router.post("/data-sources/{name}/disable")
async def disable_source(name: str):
    """Disable a registered data source by name."""
    from utils.data_sources import registry
    for s in registry._sources.values():
        if s.name == name:
            s.enabled = False
            return {"status": "disabled", "name": name}
    return {"error": f"Source '{name}' not found"}


# ── Bookmark importer endpoints ──────────────────────────────────────────────


class BookmarkImportRequest(BaseModel):
    """Bookmark import request body."""

    browser: str = "all"


@router.get("/data-sources/bookmarks/detect")
@handle_errors()
async def detect_bookmark_browsers():
    """Detect installed browsers and return bookmark counts."""
    import asyncio

    from utils.data_sources.bookmarks import detect_browsers

    return await asyncio.to_thread(detect_browsers)


@router.post("/data-sources/bookmarks/import")
@handle_errors()
async def import_browser_bookmarks(body: BookmarkImportRequest):
    """Trigger bookmark import from specified browser(s).

    Body: ``{"browser": "chrome" | "firefox" | "safari" | "all"}``
    """
    from utils.data_sources.bookmarks import import_bookmarks

    return await import_bookmarks(browser=body.browser)


@router.get("/data-sources/bookmarks/status")
@handle_errors(fallback={"last_import": None, "total_imported": 0, "total_skipped": 0, "total_errors": 0})
async def bookmark_import_status():
    """Return stats from the most recent bookmark import."""
    from utils.data_sources.bookmarks import get_import_status

    return await get_import_status()


# ── Email IMAP poller endpoints ────────────────────────────────────────────


class EmailConfigRequest(BaseModel):
    """IMAP connection configuration."""

    host: str
    port: int = 993
    user: str
    password: str
    folder: str = "INBOX"
    poll_interval: int = 15  # minutes


@router.post("/data-sources/email/configure")
@handle_errors(breaker_name="email-imap")
async def configure_email(config: EmailConfigRequest):
    """Configure IMAP connection — validates connectivity before saving."""
    from utils.data_sources.email_imap import save_email_config

    await save_email_config(config.model_dump())
    return {"status": "configured", "host": config.host, "user": config.user}


@router.get("/data-sources/email/status")
@handle_errors(fallback={"last_poll": None, "messages_ingested": 0, "errors": []})
async def email_status():
    """Return current email polling status — last poll time, message count, errors."""
    from utils.data_sources.email_imap import get_email_status

    return await get_email_status()


@router.delete("/data-sources/email")
@handle_errors()
async def delete_email_source():
    """Remove IMAP configuration and stop polling."""
    from utils.data_sources.email_imap import delete_email_config

    await delete_email_config()
    return {"status": "deleted"}


@router.post("/data-sources/email/poll-now")
@handle_errors(breaker_name="email-imap")
async def poll_email_now():
    """Trigger an immediate email poll."""
    from utils.data_sources.email_imap import poll_email

    result = await poll_email()
    return result


@router.post("/data-sources/email/import-emlx")
@handle_errors()
async def import_emlx_file(
    path: str = Query(..., description="Absolute path to .emlx file"),
):
    """Import a single Apple Mail .emlx file into the KB."""
    from utils.data_sources.email_imap import import_emlx

    result = await import_emlx(path)
    return result


# ── RSS / Atom Feed endpoints ──────────────────────────────────────────────


class AddFeedRequest(BaseModel):
    """RSS/Atom feed configuration."""

    url: str
    name: str | None = None
    domain: str = "general"


@router.post("/data-sources/rss")
@handle_errors(breaker_name="rss-feed")
async def add_rss_feed(body: AddFeedRequest):
    """Add a new RSS/Atom feed. Validates the URL is reachable and parseable."""
    from utils.data_sources.rss_feed import add_feed, validate_feed_url

    ok, message = validate_feed_url(body.url)
    if not ok:
        return {"error": f"Feed validation failed: {message}", "url": body.url}

    feed = add_feed(url=body.url, name=body.name, domain=body.domain)
    return {"status": "added", "feed": feed, "validation": message}


@router.get("/data-sources/rss")
@handle_errors(fallback={"feeds": [], "total": 0})
async def list_rss_feeds():
    """List all configured RSS/Atom feeds with their last-fetch status."""
    from utils.data_sources.rss_feed import list_feeds

    feeds = list_feeds()
    return {"feeds": feeds, "total": len(feeds)}


@router.delete("/data-sources/rss/{feed_id}")
@handle_errors()
async def delete_rss_feed(feed_id: str):
    """Remove a configured RSS/Atom feed by ID."""
    from utils.data_sources.rss_feed import remove_feed

    removed = remove_feed(feed_id)
    if not removed:
        return {"error": f"Feed '{feed_id}' not found"}
    return {"status": "removed", "feed_id": feed_id}


@router.post("/data-sources/rss/{feed_id}/fetch-now")
@handle_errors(breaker_name="rss-feed")
async def fetch_rss_feed_now(feed_id: str):
    """Immediately poll a single RSS/Atom feed."""
    from utils.data_sources.rss_feed import get_feed, poll_feed

    feed = get_feed(feed_id)
    if feed is None:
        return {"error": f"Feed '{feed_id}' not found"}
    result = await poll_feed(feed)
    return {"status": "polled", "result": result}


@router.get("/data-sources/rss/{feed_id}/entries")
@handle_errors(fallback={"entries": [], "total": 0})
async def list_rss_feed_entries(
    feed_id: str,
    limit: int = Query(default=20, ge=1, le=100, description="Max entries to return"),
):
    """List recent entries from a specific feed (fetches XML, does not re-ingest)."""
    import xml.etree.ElementTree as ET

    import httpx

    from utils.data_sources.rss_feed import (
        _detect_feed_type,
        _fetch_url,
        _parse_atom_entries,
        _parse_rss_items,
        get_feed,
    )

    feed = get_feed(feed_id)
    if feed is None:
        return {"error": f"Feed '{feed_id}' not found"}

    try:
        body, _headers = _fetch_url(feed["url"], timeout=10.0)
    except (httpx.HTTPStatusError, httpx.RequestError, OSError, ValueError) as exc:
        return {"error": f"Feed fetch failed: {exc}"}

    if body is None:
        return {"entries": [], "total": 0, "note": "304 Not Modified"}

    try:
        root = ET.fromstring(body)  # nosec B314 — RSS preview, user-configured feeds only
    except ET.ParseError as exc:
        return {"error": f"XML parse error: {exc}"}

    ftype = _detect_feed_type(root)
    items = _parse_rss_items(root) if ftype == "rss" else _parse_atom_entries(root)
    entries = items[:limit]
    return {"entries": entries, "total": len(items), "showing": len(entries)}


@router.post("/data-sources/rss/poll-all")
@handle_errors(breaker_name="rss-feed")
async def poll_all_rss_feeds():
    """Poll all enabled RSS/Atom feeds now."""
    from utils.data_sources.rss_feed import poll_all_feeds

    results = await poll_all_feeds()
    total_new = sum(r["new_entries"] for r in results)
    total_errors = sum(len(r["errors"]) for r in results)
    return {
        "status": "polled",
        "feeds_polled": len(results),
        "total_new_entries": total_new,
        "total_errors": total_errors,
        "results": results,
    }
