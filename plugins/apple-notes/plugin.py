# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: BSL-1.1

"""Apple Notes reader plugin — import notes from macOS Apple Notes.

Reads NoteStore.sqlite (read-only) from the Apple Notes container directory.
Parses note bodies stored as HTML or gzip-compressed protobuf, extracting
plain text for ingestion into the Cerid knowledge base.

Platform: macOS only (darwin).
"""

from __future__ import annotations

import logging
import re
import sqlite3
import zlib
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai-companion.plugins.apple-notes")

# Default NoteStore.sqlite location on macOS
NOTESTORE_PATH = Path.home() / "Library/Group Containers/group.com.apple.notes/NoteStore.sqlite"

# Redis key for incremental sync tracking
REDIS_LAST_SYNC_KEY = "cerid:apple-notes:last_sync"

# HTML tag stripping regex
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    """Remove HTML tags and decode common entities to plain text."""
    text = _HTML_TAG_RE.sub("", html)
    # Decode common HTML entities
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&nbsp;", " "), ("&quot;", '"'), ("&#39;", "'")]:
        text = text.replace(entity, char)
    return text.strip()


def _parse_note_body(raw_data: bytes | str | None) -> str:
    """Parse a note body from Apple Notes storage format.

    Apple Notes stores content in different formats depending on macOS version:
    - HTML (older macOS versions)
    - gzip-compressed protobuf (newer versions, typically with ZICCLOUDSYNCINGOBJECT)

    We attempt decompression first, then fall back to HTML stripping.
    """
    if raw_data is None:
        return ""

    if isinstance(raw_data, str):
        return _strip_html(raw_data)

    if not isinstance(raw_data, (bytes, bytearray)):
        return str(raw_data)

    # Try zlib/gzip decompress (compressed protobuf or compressed HTML)
    try:
        decompressed = zlib.decompress(raw_data, zlib.MAX_WBITS | 16)
        text = decompressed.decode("utf-8", errors="replace")
        # If it looks like HTML after decompression, strip tags
        if "<" in text and ">" in text:
            return _strip_html(text)
        return text.strip()
    except (zlib.error, UnicodeDecodeError):
        pass

    # Try raw zlib (no gzip header)
    try:
        decompressed = zlib.decompress(raw_data)
        text = decompressed.decode("utf-8", errors="replace")
        if "<" in text and ">" in text:
            return _strip_html(text)
        return text.strip()
    except (zlib.error, UnicodeDecodeError):
        pass

    # Try direct UTF-8 decode (uncompressed HTML or text)
    try:
        text = raw_data.decode("utf-8", errors="replace")
        if "<" in text and ">" in text:
            return _strip_html(text)
        return text.strip()
    except (UnicodeDecodeError, AttributeError):
        pass

    logger.warning("Unrecognized note body format (%d bytes), skipping", len(raw_data))
    return ""


def _get_notestore_path() -> Path:
    """Return the NoteStore.sqlite path, raising if not found."""
    if not NOTESTORE_PATH.exists():
        raise FileNotFoundError(
            f"NoteStore.sqlite not found at {NOTESTORE_PATH}. "
            "This plugin requires macOS with Apple Notes installed."
        )
    return NOTESTORE_PATH


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a read-only SQLite connection (no write, no lock conflicts)."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _folder_name_for_note(conn: sqlite3.Connection, note_row: sqlite3.Row) -> str:
    """Attempt to resolve the folder/account name for a note."""
    try:
        folder_id = note_row["ZFOLDER"]
        if folder_id is None:
            return "unfiled"
        cursor = conn.execute(
            "SELECT ZTITLE2 FROM ZICCLOUDSYNCINGOBJECT WHERE Z_PK = ? AND ZTITLE2 IS NOT NULL",
            (folder_id,),
        )
        row = cursor.fetchone()
        return row["ZTITLE2"] if row else "unfiled"
    except (KeyError, sqlite3.OperationalError):
        return "unfiled"


def _query_notes(conn: sqlite3.Connection, since_timestamp: float | None = None) -> list[dict[str, Any]]:
    """Query notes from NoteStore.sqlite.

    Args:
        since_timestamp: If provided, only return notes modified after this
            Core Data timestamp (seconds since 2001-01-01 00:00:00 UTC).

    Returns:
        List of dicts with keys: title, body, folder, modified_timestamp.
    """
    # ZICCLOUDSYNCINGOBJECT is the main table in modern Apple Notes
    # ZTITLE1 = note title, ZMODIFICATIONDATE1 = Core Data timestamp
    # ZDATA is on a related table (ZICNOTEDATA) for the note body
    base_query = """
        SELECT
            n.Z_PK,
            n.ZTITLE1 AS title,
            n.ZMODIFICATIONDATE1 AS modified_ts,
            n.ZFOLDER,
            nd.ZDATA AS body_data
        FROM ZICCLOUDSYNCINGOBJECT n
        LEFT JOIN ZICNOTEDATA nd ON nd.ZNOTE = n.Z_PK
        WHERE n.ZTITLE1 IS NOT NULL
          AND n.ZMODIFICATIONDATE1 IS NOT NULL
          AND n.ZMARKEDFORDELETION != 1
    """
    params: list[Any] = []
    if since_timestamp is not None:
        base_query += " AND n.ZMODIFICATIONDATE1 > ?"
        params.append(since_timestamp)

    base_query += " ORDER BY n.ZMODIFICATIONDATE1 DESC"

    try:
        cursor = conn.execute(base_query, params)
    except sqlite3.OperationalError as e:
        # Schema varies across macOS versions — try a simpler fallback query
        logger.warning("Primary query failed (%s), trying fallback schema", e)
        fallback_query = """
            SELECT
                Z_PK,
                ZTITLE AS title,
                ZMODIFICATIONDATE AS modified_ts,
                ZBODY AS body_data
            FROM ZNOTE
            WHERE ZTITLE IS NOT NULL
        """
        if since_timestamp is not None:
            fallback_query += " AND ZMODIFICATIONDATE > ?"
        fallback_query += " ORDER BY ZMODIFICATIONDATE DESC"
        cursor = conn.execute(fallback_query, params)

    notes: list[dict[str, Any]] = []
    for row in cursor:
        title = row["title"] or "Untitled"
        body_text = _parse_note_body(row["body_data"])
        modified_ts = row["modified_ts"]

        folder = "unfiled"
        try:
            folder = _folder_name_for_note(conn, row)
        except (KeyError, sqlite3.OperationalError):
            pass

        if not body_text and not title:
            continue

        notes.append({
            "title": title,
            "body": body_text,
            "folder": folder,
            "modified_timestamp": modified_ts,
        })

    return notes


def import_all() -> list[dict[str, Any]]:
    """Import all notes from Apple Notes.

    Returns:
        List of ingestion results from ingest_content().
    """
    from config.features import check_feature
    check_feature("apple_notes_reader")

    db_path = _get_notestore_path()
    conn = _connect_readonly(db_path)

    try:
        notes = _query_notes(conn)
    finally:
        conn.close()

    logger.info("Apple Notes: found %d notes for import", len(notes))
    return _ingest_notes(notes)


def import_new(since_timestamp: float | None = None) -> list[dict[str, Any]]:
    """Import notes modified since the given timestamp (incremental sync).

    If since_timestamp is None, reads last sync time from Redis.
    Updates Redis with the current sync timestamp on success.

    Args:
        since_timestamp: Core Data timestamp (seconds since 2001-01-01).
            If None, uses the last stored sync time from Redis.

    Returns:
        List of ingestion results from ingest_content().
    """
    from config.features import check_feature
    check_feature("apple_notes_reader")

    # Resolve timestamp from Redis if not provided
    if since_timestamp is None:
        try:
            from deps import get_redis
            redis_client = get_redis()
            if redis_client is not None:
                stored = redis_client.get(REDIS_LAST_SYNC_KEY)
                if stored is not None:
                    since_timestamp = float(stored)
        except (ValueError, OSError, RuntimeError) as e:
            logger.warning("Failed to read last sync time from Redis: %s", e)

    db_path = _get_notestore_path()
    conn = _connect_readonly(db_path)

    try:
        notes = _query_notes(conn, since_timestamp=since_timestamp)
    finally:
        conn.close()

    logger.info("Apple Notes: found %d new/updated notes since %s", len(notes), since_timestamp)

    results = _ingest_notes(notes)

    # Store current sync timestamp in Redis
    if notes:
        latest_ts = max(n["modified_timestamp"] for n in notes if n["modified_timestamp"] is not None)
        try:
            from deps import get_redis
            redis_client = get_redis()
            if redis_client is not None and latest_ts is not None:
                redis_client.set(REDIS_LAST_SYNC_KEY, str(latest_ts))
                logger.info("Apple Notes: updated last sync timestamp to %s", latest_ts)
        except (OSError, RuntimeError) as e:
            logger.warning("Failed to store last sync time in Redis: %s", e)

    return results


def _ingest_notes(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ingest a list of parsed notes into the knowledge base.

    Args:
        notes: List of dicts from _query_notes().

    Returns:
        List of ingestion result dicts.
    """
    from services.ingestion import ingest_content

    results: list[dict[str, Any]] = []
    for note in notes:
        content = note["body"]
        if note["title"] and note["title"] != "Untitled":
            content = f"{note['title']}\n\n{content}"

        if not content.strip():
            continue

        # Derive sub-category from folder name
        folder = note.get("folder", "unfiled")
        sub_category = folder.lower().replace(" ", "-") if folder else "unfiled"

        metadata = {
            "filename": f"apple-note-{note['title'][:50]}.txt",
            "domain": "notes",
            "sub_category": sub_category,
            "source": "apple-notes",
            "client_source": "apple-notes-plugin",
            "tags_json": '["apple-notes", "imported"]',
        }

        try:
            result = ingest_content(content, domain="notes", metadata=metadata)
            results.append(result)
        except (ValueError, OSError, RuntimeError) as e:
            logger.error("Failed to ingest note '%s': %s", note["title"], e)
            results.append({"status": "error", "error": str(e), "title": note["title"]})

    return results


def register() -> None:
    """Register the Apple Notes plugin. No parser registration needed — this is an ingestion plugin."""
    logger.info("Apple Notes plugin registered (import_all / import_new)")
