# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Calendar-aware meeting stitching.

When a calendar connector is registered (Google Calendar via MCP or
Apple Calendar via EventKit), this module matches the recording's
timestamp window against scheduled events. A match produces:

  - calendar_event_id          — for back-linking from the KB artifact
  - attendees                  — list of names/emails (feeds pyannote's
                                 max_speakers parameter on the next run)
  - event_title + event_time   — surfaced in the meeting summary header

If no calendar connector is registered, this is a clean no-op: the meeting
gets ingested without calendar metadata. This is the documented Granola-
parity behavior per the audio-stack research (§8).
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai-companion.plugins.meeting_capture.calendar_stitch")


def _overlap_seconds(
    a_start: _dt.datetime, a_end: _dt.datetime,
    b_start: _dt.datetime, b_end: _dt.datetime,
) -> float:
    """Overlap duration between two intervals, in seconds (0 if disjoint)."""
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    delta = (end - start).total_seconds()
    return max(0.0, delta)


def _recording_window(file_path: str, duration_seconds: float) -> tuple[_dt.datetime, _dt.datetime]:
    """Infer the wall-clock window the recording covers.

    Heuristic: file mtime is the end of recording (when the file was
    closed). Subtract duration to get the start. Operators capturing
    audio via an external device may need a metadata override later
    (e.g., reading EXIF time from .mp4); for v0.1 mtime is enough.
    """
    p = Path(file_path)
    end = _dt.datetime.fromtimestamp(p.stat().st_mtime, tz=_dt.timezone.utc)
    start = end - _dt.timedelta(seconds=duration_seconds)
    return start, end


async def match_to_event(file_path: str, duration_seconds: float) -> dict[str, Any] | None:
    """Find the calendar event whose window overlaps the recording.

    Returns a dict suitable for merging into the meeting result, or None
    when no calendar is configured or no event matches.

    Async because the Phase F GoogleCalendarDataSource's list_events is
    async (talks to a sibling MCP server via the streamable-HTTP client).
    Future sync calendar sources (e.g. Apple EventKit via Swift helper)
    will be wrapped in asyncio.to_thread by their DataSource subclass
    so callers stay uniformly async.
    """
    import inspect

    # Lazy import — the data source registry is in app/, but plugins/
    # is allowed to read from it (one-way: plugins can use app helpers).
    try:
        from app.data_sources import registry
    except ImportError:
        logger.debug("DataSourceRegistry not available; skipping calendar stitch")
        return None

    # Calendar-specific DataSource subclass conforms to CalendarDataSource
    # Protocol (app/data_sources/calendar_protocol.py). Typed as Any here
    # because the registry returns the base DataSource type — the runtime
    # check below verifies list_events exists before invoking it.
    # Fallback chain (most-specific to least): Google → Outlook →
    # Apple (Swift helper) → legacy Apple key. The 'apple_calendar' key
    # is registered by plugins/apple_calendar (Phase G.4); the legacy
    # 'apple_calendar_eventkit' key was reserved earlier in the plan
    # before the helper landed — kept as a tail entry so any
    # ephemeral references in tests resolve without an extra
    # migration.
    calendar_source: Any = (
        registry.get("google_calendar")
        or registry.get("outlook_calendar")
        or registry.get("apple_calendar")
        or registry.get("apple_calendar_eventkit")
    )
    if calendar_source is None:
        return None  # no calendar connected — clean no-op
    if not hasattr(calendar_source, "list_events"):
        logger.warning(
            "DataSource %s does not implement list_events; cannot stitch",
            getattr(calendar_source, "name", "?"),
        )
        return None

    rec_start, rec_end = _recording_window(file_path, duration_seconds)
    window_padding = int(os.getenv("CALENDAR_STITCH_WINDOW_SECONDS", "300"))
    query_start = rec_start - _dt.timedelta(seconds=window_padding)
    query_end = rec_end + _dt.timedelta(seconds=window_padding)

    try:
        result = calendar_source.list_events(start=query_start, end=query_end)
        events = await result if inspect.isawaitable(result) else result
    except (ValueError, OSError, RuntimeError) as exc:
        logger.warning("Calendar source query failed; soft-skip stitching: %s", exc)
        return None

    if not events:
        return None

    # Pick the event with the largest overlap (handles back-to-back meetings)
    best_event = None
    best_overlap = 0.0
    for ev in events:
        ev_start = ev.get("start")
        ev_end = ev.get("end")
        if not ev_start or not ev_end:
            continue
        overlap = _overlap_seconds(rec_start, rec_end, ev_start, ev_end)
        if overlap > best_overlap:
            best_overlap = overlap
            best_event = ev

    if best_event is None or best_overlap == 0:
        return None

    # Require ≥ 80% of the recording falls within the event window — guards
    # against false-positive matches on noisy back-to-back schedules.
    recording_duration_s = (rec_end - rec_start).total_seconds() or 1.0
    coverage = best_overlap / recording_duration_s
    if coverage < 0.8:
        logger.info(
            "Calendar event %s only covers %.1f%% of the recording window; "
            "below 80%% threshold, skipping stitch",
            best_event.get("id"), coverage * 100,
        )
        return None

    return {
        "calendar_event_id": best_event.get("id"),
        "calendar_event_title": best_event.get("title"),
        "calendar_event_start": best_event.get("start"),
        "attendees": best_event.get("attendees", []),
    }
