# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Browser Bookmark Importer — reads bookmarks from Chrome, Firefox, Safari on macOS.

Parses local bookmark stores and ingests title + URL metadata into the knowledge
base via ``ingest_content()``. Does NOT fetch page content to avoid rate limiting
and slow imports.

Supported browsers:
- Chrome: ``~/Library/Application Support/Google/Chrome/Default/Bookmarks`` (JSON)
- Firefox: ``~/Library/Application Support/Firefox/Profiles/*/places.sqlite`` (SQLite)
- Safari: ``~/Library/Safari/Bookmarks.plist`` (binary plist)

Dependencies: stdlib only (json, sqlite3, plistlib, pathlib).
"""
from __future__ import annotations

import asyncio
import glob
import hashlib
import json
import logging
import plistlib
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import DataSource, DataSourceResult

logger = logging.getLogger("ai-companion.data_sources.bookmarks")

__all__ = ["BookmarksSource", "detect_browsers", "import_bookmarks", "get_import_status"]

# ── Bookmark data model ──────────────────────────────────────────────────────


@dataclass
class Bookmark:
    """A single parsed bookmark entry."""

    name: str
    url: str
    folder_path: str = ""
    date_added: str = ""
    browser: str = ""


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


# ── Chrome reader ─────────────────────────────────────────────────────────────

_CHROME_BOOKMARKS_PATH = Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "Bookmarks"


def _chrome_epoch_to_iso(chrome_ts: str) -> str:
    """Convert Chrome's WebKit timestamp (microseconds since 1601-01-01) to ISO."""
    try:
        ts = int(chrome_ts)
        # Chrome epoch offset: 11644473600 seconds between 1601 and 1970
        unix_ts = (ts / 1_000_000) - 11_644_473_600
        if unix_ts < 0:
            return ""
        return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return ""


def _walk_chrome_tree(node: dict, path_parts: list[str]) -> list[Bookmark]:
    """Recursively walk Chrome bookmark JSON tree."""
    bookmarks: list[Bookmark] = []
    node_type = node.get("type", "")

    if node_type == "url":
        url = node.get("url", "")
        if url and url.startswith(("http://", "https://")):
            bookmarks.append(Bookmark(
                name=node.get("name", ""),
                url=url,
                folder_path="/".join(path_parts),
                date_added=_chrome_epoch_to_iso(node.get("date_added", "0")),
                browser="chrome",
            ))
    elif node_type == "folder":
        folder_name = node.get("name", "")
        child_path = [*path_parts, folder_name] if folder_name else path_parts
        for child in node.get("children", []):
            bookmarks.extend(_walk_chrome_tree(child, child_path))

    return bookmarks


def read_chrome_bookmarks() -> list[Bookmark]:
    """Read bookmarks from the Chrome Default profile."""
    if not _CHROME_BOOKMARKS_PATH.exists():
        logger.debug("Chrome bookmarks file not found: %s", _CHROME_BOOKMARKS_PATH)
        return []

    try:
        data = json.loads(_CHROME_BOOKMARKS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read Chrome bookmarks: %s", exc)
        return []

    bookmarks: list[Bookmark] = []
    roots = data.get("roots", {})
    for root_name, root_node in roots.items():
        if isinstance(root_node, dict):
            bookmarks.extend(_walk_chrome_tree(root_node, [root_name]))

    logger.info("Read %d Chrome bookmarks", len(bookmarks))
    return bookmarks


# ── Firefox reader ────────────────────────────────────────────────────────────

_FIREFOX_PROFILES_GLOB = str(
    Path.home() / "Library" / "Application Support" / "Firefox" / "Profiles" / "*" / "places.sqlite"
)


def _firefox_epoch_to_iso(micro_ts: int) -> str:
    """Convert Firefox microsecond timestamp to ISO."""
    try:
        unix_ts = micro_ts / 1_000_000
        if unix_ts < 0:
            return ""
        return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return ""


def _firefox_folder_path(conn: sqlite3.Connection, parent_id: int) -> str:
    """Build folder path by walking parent chain."""
    parts: list[str] = []
    seen: set[int] = set()
    current = parent_id
    while current and current not in seen:
        seen.add(current)
        row = conn.execute(
            "SELECT title, parent FROM moz_bookmarks WHERE id = ?", (current,)
        ).fetchone()
        if not row:
            break
        title, parent = row
        if title:
            parts.append(title)
        current = parent
    parts.reverse()
    return "/".join(parts)


def read_firefox_bookmarks() -> list[Bookmark]:
    """Read bookmarks from all Firefox profiles (read-only)."""
    db_paths = glob.glob(_FIREFOX_PROFILES_GLOB)
    if not db_paths:
        logger.debug("No Firefox places.sqlite found")
        return []

    all_bookmarks: list[Bookmark] = []
    for db_path in db_paths:
        try:
            # Read-only URI connection to avoid locking the browser's DB
            uri = f"file:{db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            try:
                rows = conn.execute(
                    "SELECT b.title, p.url, b.dateAdded, b.parent "
                    "FROM moz_bookmarks b "
                    "JOIN moz_places p ON b.fk = p.id "
                    "WHERE b.type = 1"
                ).fetchall()

                for title, url, date_added, parent_id in rows:
                    if not url or not url.startswith(("http://", "https://")):
                        continue
                    folder = _firefox_folder_path(conn, parent_id)
                    all_bookmarks.append(Bookmark(
                        name=title or "",
                        url=url,
                        folder_path=folder,
                        date_added=_firefox_epoch_to_iso(date_added or 0),
                        browser="firefox",
                    ))
            finally:
                conn.close()
        except sqlite3.OperationalError as exc:
            logger.warning("Cannot read Firefox DB %s (browser may be running): %s", db_path, exc)
        except (sqlite3.Error, OSError) as exc:
            logger.warning("Failed to read Firefox DB %s: %s", db_path, exc)

    logger.info("Read %d Firefox bookmarks", len(all_bookmarks))
    return all_bookmarks


# ── Safari reader ─────────────────────────────────────────────────────────────

_SAFARI_BOOKMARKS_PATH = Path.home() / "Library" / "Safari" / "Bookmarks.plist"


def _walk_safari_plist(node: dict, path_parts: list[str]) -> list[Bookmark]:
    """Recursively walk Safari plist bookmark tree."""
    bookmarks: list[Bookmark] = []
    web_bookmark_type = node.get("WebBookmarkType", "")

    if web_bookmark_type == "WebBookmarkTypeLeaf":
        url = node.get("URLString", "")
        if url and url.startswith(("http://", "https://")):
            # Detect Reading List entries
            is_reading_list = "ReadingList" in node
            folder = "ReadingList" if is_reading_list else "/".join(path_parts)
            bookmarks.append(Bookmark(
                name=node.get("URIDictionary", {}).get("title", ""),
                url=url,
                folder_path=folder,
                date_added="",
                browser="safari",
            ))
    elif web_bookmark_type == "WebBookmarkTypeList":
        folder_title = node.get("Title", "")
        child_path = [*path_parts, folder_title] if folder_title else path_parts
        for child in node.get("Children", []):
            bookmarks.extend(_walk_safari_plist(child, child_path))
    elif web_bookmark_type == "WebBookmarkTypeProxy":
        # Reading List proxy container
        for child in node.get("Children", []):
            bookmarks.extend(_walk_safari_plist(child, ["ReadingList"]))

    return bookmarks


def read_safari_bookmarks() -> list[Bookmark]:
    """Read bookmarks from Safari (binary plist)."""
    if not _SAFARI_BOOKMARKS_PATH.exists():
        logger.debug("Safari bookmarks file not found: %s", _SAFARI_BOOKMARKS_PATH)
        return []

    try:
        with open(_SAFARI_BOOKMARKS_PATH, "rb") as f:
            plist_data = plistlib.load(f)
    except (plistlib.InvalidFileException, OSError) as exc:
        logger.warning("Failed to read Safari bookmarks: %s", exc)
        return []

    bookmarks = _walk_safari_plist(plist_data, [])
    logger.info("Read %d Safari bookmarks", len(bookmarks))
    return bookmarks


# ── Domain mapping ────────────────────────────────────────────────────────────

def _folder_to_subcategory(folder_path: str) -> str:
    """Map browser folder hierarchy to a kebab-case sub-category slug.

    Examples:
        ``Tech/AI`` → ``tech-ai``
        ``ReadingList`` → ``reading-list``
    """
    if not folder_path:
        return ""
    # Normalize separators and collapse
    slug = folder_path.strip("/").replace("/", "-").lower()
    # Remove non-alphanumeric except hyphens
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or ""


# ── Detection ────────────────────────────────────────────────────────────────

_READERS: dict[str, Any] = {
    "chrome": read_chrome_bookmarks,
    "firefox": read_firefox_bookmarks,
    "safari": read_safari_bookmarks,
}


def detect_browsers() -> dict[str, dict[str, Any]]:
    """Detect installed browsers and return bookmark counts."""
    detected: dict[str, dict[str, Any]] = {}

    if _CHROME_BOOKMARKS_PATH.exists():
        try:
            count = len(read_chrome_bookmarks())
            detected["chrome"] = {"installed": True, "bookmark_count": count}
        except (OSError, RuntimeError, json.JSONDecodeError):
            detected["chrome"] = {"installed": True, "bookmark_count": -1}
    else:
        detected["chrome"] = {"installed": False, "bookmark_count": 0}

    if glob.glob(_FIREFOX_PROFILES_GLOB):
        try:
            count = len(read_firefox_bookmarks())
            detected["firefox"] = {"installed": True, "bookmark_count": count}
        except (OSError, RuntimeError, sqlite3.Error):
            detected["firefox"] = {"installed": True, "bookmark_count": -1}
    else:
        detected["firefox"] = {"installed": False, "bookmark_count": 0}

    if _SAFARI_BOOKMARKS_PATH.exists():
        try:
            count = len(read_safari_bookmarks())
            detected["safari"] = {"installed": True, "bookmark_count": count}
        except (OSError, RuntimeError, plistlib.InvalidFileException):
            detected["safari"] = {"installed": True, "bookmark_count": -1}
    else:
        detected["safari"] = {"installed": False, "bookmark_count": 0}

    return detected


# ── Import orchestrator ───────────────────────────────────────────────────────

_REDIS_SEEN_KEY = "cerid:bookmarks:seen"
_REDIS_STATUS_KEY = "cerid:bookmarks:status"


async def import_bookmarks(browser: str = "all") -> dict[str, Any]:
    """Import bookmarks from specified browser(s) into the knowledge base.

    Stores URL and title as metadata — does NOT fetch page content to avoid
    rate limiting and slow imports.

    Args:
        browser: ``chrome``, ``firefox``, ``safari``, or ``all``.

    Returns:
        Dict with import counts (imported, skipped, errors).
    """
    from services.ingestion import ingest_content

    if browser == "all":
        targets = list(_READERS.keys())
    elif browser in _READERS:
        targets = [browser]
    else:
        raise ValueError(f"Unknown browser: {browser}. Use chrome, firefox, safari, or all.")

    # Load dedup set from Redis
    seen: set[str] = set()
    redis_client = None
    try:
        from deps import get_redis
        redis_client = get_redis()
        if redis_client:
            existing = redis_client.smembers(_REDIS_SEEN_KEY)
            if existing:
                seen = {m.decode("utf-8") if isinstance(m, bytes) else m for m in existing}
    except (OSError, RuntimeError) as exc:
        logger.debug("Redis dedup set unavailable: %s", exc)

    start = time.monotonic()
    imported = 0
    skipped = 0
    errors = 0

    for target in targets:
        # Read bookmarks (sync I/O — run in thread pool)
        try:
            bookmarks = await asyncio.to_thread(_READERS[target])
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            logger.warning("Failed to read %s bookmarks: %s", target, exc)
            errors += 1
            continue

        for bm in bookmarks:
            url_h = _url_hash(bm.url)

            # Skip already-seen URLs
            if url_h in seen:
                skipped += 1
                continue

            try:
                # Build content — store title and URL only (no page fetch)
                content = f"Bookmark: {bm.name}\nURL: {bm.url}"
                if bm.folder_path:
                    content += f"\nFolder: {bm.folder_path}"

                subcategory = _folder_to_subcategory(bm.folder_path)
                if bm.browser == "safari" and bm.folder_path == "ReadingList":
                    subcategory = "reading-list"

                metadata: dict[str, Any] = {
                    "source": f"bookmark:{bm.browser}",
                    "source_url": bm.url,
                    "bookmark_name": bm.name,
                    "bookmark_folder": bm.folder_path,
                    "filename": f"bookmark_{url_h[:12]}.txt",
                }
                if bm.date_added:
                    metadata["date_added"] = bm.date_added
                if subcategory:
                    metadata["sub_category"] = subcategory

                await asyncio.to_thread(
                    ingest_content, content, "bookmarks", metadata,
                )

                # Mark as seen
                seen.add(url_h)
                if redis_client:
                    try:
                        redis_client.sadd(_REDIS_SEEN_KEY, url_h)
                    except (OSError, RuntimeError):
                        pass

                imported += 1

            except (ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
                logger.warning("Failed to ingest bookmark %s: %s", bm.url[:80], exc)
                errors += 1

    duration = round(time.monotonic() - start, 2)

    # Persist status to Redis
    await _update_import_status(imported, skipped, errors, browser, duration)

    return {
        "status": "ok",
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "duration_seconds": duration,
    }


async def get_import_status() -> dict[str, Any]:
    """Return last import stats from Redis."""
    try:
        from deps import get_redis
        r = get_redis()
        if r:
            raw = r.get(_REDIS_STATUS_KEY)
            if raw:
                return json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
    except (OSError, RuntimeError):
        pass
    return {"last_import": None, "total_imported": 0, "total_skipped": 0, "total_errors": 0}


async def _update_import_status(
    imported: int, skipped: int, errors: int, browser: str, duration: float,
) -> None:
    """Persist import stats to Redis."""
    try:
        from deps import get_redis
        r = get_redis()
        if not r:
            return
        existing: dict[str, Any] = {}
        raw = r.get(_REDIS_STATUS_KEY)
        if raw:
            existing = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
        existing["last_import"] = datetime.now(timezone.utc).isoformat()
        existing["last_browser"] = browser
        existing["total_imported"] = existing.get("total_imported", 0) + imported
        existing["total_skipped"] = existing.get("total_skipped", 0) + skipped
        existing["total_errors"] = existing.get("total_errors", 0) + errors
        existing["last_imported"] = imported
        existing["last_skipped"] = skipped
        existing["last_errors"] = errors
        existing["last_duration_seconds"] = duration
        r.set(_REDIS_STATUS_KEY, json.dumps(existing))
    except (OSError, RuntimeError) as exc:
        logger.debug("Failed to update bookmark import status: %s", exc)


# ── DataSource interface (for registry) ───────────────────────────────────────

class BookmarksSource(DataSource):
    """Bookmark importer data source.

    Unlike other data sources, this doesn't query external APIs in real time.
    The ``query()`` method returns empty results — bookmark data is ingested
    into the KB via the ``import_bookmarks()`` function and the REST endpoints.
    """

    name = "bookmarks"
    description = "Browser bookmark importer (Chrome, Firefox, Safari). Ingests bookmarked pages into KB."
    requires_api_key = False
    domains: list[str] = ["bookmarks"]

    async def query(self, query: str, **kwargs: Any) -> list[DataSourceResult]:
        """Bookmarks are ingested, not queried in real time. Returns empty."""
        return []

    def is_configured(self) -> bool:
        """Always configured — reads local files, no API key needed."""
        return True
