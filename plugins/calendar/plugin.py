# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: BSL-1.1

"""Calendar sync plugin — ICS file parsing and Apple Calendar integration.

Supports:
- Parsing standard .ics files (VEVENT extraction, no external dependencies)
- Reading Apple Calendar from ~/Library/Calendars/ (.ics files) or
  Calendar.sqlitedb from ~/Library/Calendars/Calendar Cache/
- Auto-categorization of events: meetings, deadlines, personal

No external dependencies — uses stdlib only.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai-companion.plugins.calendar")

# Apple Calendar locations on macOS
APPLE_CALENDARS_DIR = Path.home() / "Library/Calendars"
APPLE_CALENDAR_CACHE_DB = Path.home() / "Library/Calendars/Calendar Cache/Calendar.sqlitedb"

# Keywords for auto-categorization
_MEETING_KEYWORDS = {"meeting", "standup", "sync", "review", "retro", "1:1", "1-on-1",
                     "call", "interview", "demo", "presentation", "sprint", "planning"}
_DEADLINE_KEYWORDS = {"deadline", "due", "submit", "delivery", "launch", "release",
                      "milestone", "cutoff", "final"}


# ---------------------------------------------------------------------------
# ICS Parser (stdlib, no icalendar dependency)
# ---------------------------------------------------------------------------

def _unfold_ics_lines(raw: str) -> list[str]:
    """Unfold continuation lines per RFC 5545 (lines starting with space/tab)."""
    lines: list[str] = []
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _parse_ics_value(line: str) -> tuple[str, dict[str, str], str]:
    """Parse an ICS content line into (name, params, value).

    Example: 'DTSTART;TZID=America/New_York:20240101T090000'
    Returns: ('DTSTART', {'TZID': 'America/New_York'}, '20240101T090000')
    """
    # Split property name + params from value
    colon_idx = line.find(":")
    if colon_idx < 0:
        return line, {}, ""

    name_part = line[:colon_idx]
    value = line[colon_idx + 1:]

    # Split name from params
    params: dict[str, str] = {}
    if ";" in name_part:
        parts = name_part.split(";")
        name = parts[0]
        for p in parts[1:]:
            if "=" in p:
                pk, pv = p.split("=", 1)
                params[pk] = pv
    else:
        name = name_part

    return name.upper(), params, value


def _parse_ics_datetime(value: str) -> str | None:
    """Parse an ICS datetime string to ISO 8601 format.

    Handles: 20240101T090000, 20240101T090000Z, 20240101
    """
    value = value.strip()
    if not value:
        return None

    # Remove trailing Z for parsing, note it as UTC
    is_utc = value.endswith("Z")
    value = value.rstrip("Z")

    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            dt = datetime.strptime(value, fmt)
            iso = dt.isoformat()
            if is_utc:
                iso += "Z"
            return iso
        except ValueError:
            continue
    return value  # Return raw if unparseable


def parse_ics(content: str) -> list[dict[str, Any]]:
    """Parse an ICS file content string and extract VEVENT blocks.

    Args:
        content: Raw .ics file content.

    Returns:
        List of event dicts with keys: title, description, location,
        start, end, attendees.
    """
    lines = _unfold_ics_lines(content)
    events: list[dict[str, Any]] = []
    in_event = False
    current: dict[str, Any] = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line == "BEGIN:VEVENT":
            in_event = True
            current = {
                "title": "",
                "description": "",
                "location": "",
                "start": None,
                "end": None,
                "attendees": [],
            }
            continue

        if line == "END:VEVENT":
            in_event = False
            if current.get("title") or current.get("start"):
                events.append(current)
            current = {}
            continue

        if not in_event:
            continue

        name, params, value = _parse_ics_value(line)

        if name == "SUMMARY":
            current["title"] = value
        elif name == "DESCRIPTION":
            current["description"] = value.replace("\\n", "\n").replace("\\,", ",")
        elif name == "LOCATION":
            current["location"] = value.replace("\\,", ",")
        elif name == "DTSTART":
            current["start"] = _parse_ics_datetime(value)
        elif name == "DTEND":
            current["end"] = _parse_ics_datetime(value)
        elif name == "ATTENDEE":
            # Extract email from ATTENDEE:mailto:foo@bar.com or CN param
            email = value
            if email.lower().startswith("mailto:"):
                email = email[7:]
            cn = params.get("CN", "").strip('"')
            attendee = cn if cn else email
            if attendee:
                current["attendees"].append(attendee)

    return events


def parse_ics_file(file_path: str) -> dict[str, Any]:
    """Parse a .ics file from disk and return structured event data.

    Args:
        file_path: Path to the .ics file.

    Returns:
        {"text": str, "file_type": ".ics", "events": list, "page_count": None}
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"ICS file not found: {file_path}")

    content = path.read_text(encoding="utf-8", errors="replace")
    events = parse_ics(content)

    # Build plain text representation for ingestion
    text_parts: list[str] = []
    for evt in events:
        lines = [f"Event: {evt['title']}"]
        if evt["start"]:
            lines.append(f"Start: {evt['start']}")
        if evt["end"]:
            lines.append(f"End: {evt['end']}")
        if evt["location"]:
            lines.append(f"Location: {evt['location']}")
        if evt["attendees"]:
            lines.append(f"Attendees: {', '.join(evt['attendees'])}")
        if evt["description"]:
            lines.append(f"Description: {evt['description']}")
        text_parts.append("\n".join(lines))

    text = "\n\n---\n\n".join(text_parts)

    return {
        "text": text,
        "file_type": ".ics",
        "events": events,
        "page_count": None,
    }


# ---------------------------------------------------------------------------
# Auto-categorization
# ---------------------------------------------------------------------------

def categorize_event(event: dict[str, Any]) -> str:
    """Auto-categorize an event as meetings, deadlines, or personal.

    Rules:
    - attendee count > 2 = meetings
    - deadline keywords in title = deadlines
    - otherwise = personal
    """
    title_lower = (event.get("title") or "").lower()

    # Check attendee count first (meetings)
    attendees = event.get("attendees", [])
    if len(attendees) > 2:
        return "meetings"

    # Check for meeting keywords
    for keyword in _MEETING_KEYWORDS:
        if keyword in title_lower:
            return "meetings"

    # Check for deadline keywords
    for keyword in _DEADLINE_KEYWORDS:
        if keyword in title_lower:
            return "deadlines"

    return "personal"


# ---------------------------------------------------------------------------
# Apple Calendar reader (macOS)
# ---------------------------------------------------------------------------

def _find_apple_ics_files() -> list[Path]:
    """Find all .ics files in ~/Library/Calendars/."""
    if not APPLE_CALENDARS_DIR.exists():
        return []
    return list(APPLE_CALENDARS_DIR.rglob("*.ics"))


def _read_apple_calendar_sqlite() -> list[dict[str, Any]]:
    """Read events from Apple Calendar's SQLite cache (Calendar.sqlitedb).

    Returns:
        List of event dicts with keys: title, description, location,
        start, end, attendees.
    """
    if not APPLE_CALENDAR_CACHE_DB.exists():
        return []

    uri = f"file:{APPLE_CALENDAR_CACHE_DB}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as e:
        logger.warning("Cannot open Calendar.sqlitedb: %s", e)
        return []

    events: list[dict[str, Any]] = []
    try:
        # CalendarItem is the main events table in Calendar.sqlitedb
        cursor = conn.execute("""
            SELECT
                ci.ZSUMMARY AS title,
                ci.ZNOTES AS description,
                ci.ZLOCATION AS location,
                ci.ZSTARTDATE AS start_ts,
                ci.ZENDDATE AS end_ts
            FROM ZCALENDARITEM ci
            WHERE ci.ZSUMMARY IS NOT NULL
            ORDER BY ci.ZSTARTDATE DESC
            LIMIT 500
        """)

        for row in cursor:
            # Core Data timestamps: seconds since 2001-01-01 00:00:00 UTC
            start_iso = None
            end_iso = None
            if row["start_ts"] is not None:
                try:
                    # Core Data epoch offset from Unix epoch
                    cd_epoch_offset = 978307200
                    start_iso = datetime.utcfromtimestamp(row["start_ts"] + cd_epoch_offset).isoformat() + "Z"
                except (ValueError, OSError):
                    pass
            if row["end_ts"] is not None:
                try:
                    cd_epoch_offset = 978307200
                    end_iso = datetime.utcfromtimestamp(row["end_ts"] + cd_epoch_offset).isoformat() + "Z"
                except (ValueError, OSError):
                    pass

            events.append({
                "title": row["title"] or "",
                "description": row["description"] or "",
                "location": row["location"] or "",
                "start": start_iso,
                "end": end_iso,
                "attendees": [],  # Attendees are in a separate relation table
            })

        # Try to fetch attendees from the Attendee table
        try:
            att_cursor = conn.execute("""
                SELECT
                    a.ZCOMMONNAME AS name,
                    a.ZADDRESS AS email,
                    a.ZOWNER AS event_pk
                FROM ZATTENDEE a
                WHERE a.ZOWNER IS NOT NULL
            """)
            attendee_map: dict[int, list[str]] = {}
            for att_row in att_cursor:
                pk = att_row["event_pk"]
                name = att_row["name"] or att_row["email"] or ""
                if name.lower().startswith("mailto:"):
                    name = name[7:]
                if name:
                    attendee_map.setdefault(pk, []).append(name)
            # We don't have event PKs mapped here, so attendees from sqlite
            # are best-effort. For full attendee support, use ICS files.
        except sqlite3.OperationalError:
            pass  # Attendee table schema may vary

    except sqlite3.OperationalError as e:
        logger.warning("Failed to query Calendar.sqlitedb: %s", e)
    finally:
        conn.close()

    return events


def import_apple_calendar() -> list[dict[str, Any]]:
    """Import events from Apple Calendar (ICS files + SQLite cache).

    Returns:
        List of ingestion results.
    """
    from config.features import check_feature
    check_feature("calendar_sync")

    all_events: list[dict[str, Any]] = []

    # Strategy 1: Read .ics files from ~/Library/Calendars/
    ics_files = _find_apple_ics_files()
    for ics_path in ics_files:
        try:
            content = ics_path.read_text(encoding="utf-8", errors="replace")
            events = parse_ics(content)
            all_events.extend(events)
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Failed to read ICS file %s: %s", ics_path, e)

    # Strategy 2: Read from SQLite cache (may have more complete data)
    if not all_events:
        sqlite_events = _read_apple_calendar_sqlite()
        all_events.extend(sqlite_events)

    logger.info("Apple Calendar: found %d events for import", len(all_events))
    return _ingest_events(all_events)


def import_ics_content(content: str) -> list[dict[str, Any]]:
    """Import events from raw ICS content (for the upload endpoint).

    Args:
        content: Raw .ics file content string.

    Returns:
        List of ingestion results.
    """
    from config.features import check_feature
    check_feature("calendar_sync")

    events = parse_ics(content)
    logger.info("ICS import: parsed %d events from uploaded content", len(events))
    return _ingest_events(events)


def _ingest_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ingest a list of parsed calendar events into the knowledge base.

    Args:
        events: List of event dicts from parse_ics() or Apple Calendar reader.

    Returns:
        List of ingestion result dicts.
    """
    import json

    from services.ingestion import ingest_content

    results: list[dict[str, Any]] = []
    for event in events:
        title = event.get("title", "Untitled Event")
        if not title.strip():
            continue

        # Build ingestion content
        lines = [f"Calendar Event: {title}"]
        if event.get("start"):
            lines.append(f"Start: {event['start']}")
        if event.get("end"):
            lines.append(f"End: {event['end']}")
        if event.get("location"):
            lines.append(f"Location: {event['location']}")
        if event.get("attendees"):
            lines.append(f"Attendees: {', '.join(event['attendees'])}")
        if event.get("description"):
            lines.append(f"\n{event['description']}")

        content = "\n".join(lines)

        # Auto-categorize
        sub_category = categorize_event(event)

        tags = ["calendar", sub_category]
        if event.get("attendees"):
            tags.append("has-attendees")

        metadata = {
            "filename": f"calendar-{title[:50]}.txt",
            "domain": "calendar",
            "sub_category": sub_category,
            "source": "calendar-plugin",
            "client_source": "calendar-plugin",
            "tags_json": json.dumps(tags),
        }

        try:
            result = ingest_content(content, domain="calendar", metadata=metadata)
            results.append(result)
        except (ValueError, OSError, RuntimeError) as e:
            logger.error("Failed to ingest event '%s': %s", title, e)
            results.append({"status": "error", "error": str(e), "title": title})

    return results


def register() -> None:
    """Register the calendar plugin — adds .ics parser to the parser registry."""
    from parsers.registry import PARSER_REGISTRY

    if ".ics" in PARSER_REGISTRY:
        logger.info("Calendar plugin overriding parser for .ics")
    PARSER_REGISTRY[".ics"] = parse_ics_file

    logger.info("Calendar plugin registered (.ics parser + Apple Calendar reader)")
