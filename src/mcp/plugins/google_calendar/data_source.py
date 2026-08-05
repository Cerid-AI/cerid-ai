# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""Google Calendar DataSource — Phase F Day 3.

Implements the CalendarDataSource Protocol on top of the sibling
google-workspace-mcp container. Used by:

  1. The query agent's data-source fan-out (``DataSource.query()`` —
     "what's on my calendar tomorrow?")
  2. The meeting_capture plugin's calendar_stitch.match_to_event lookup
     (``CalendarDataSource.list_events(start, end)`` — resolve which
     event a meeting recording belongs to).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from app.data_sources.base import DataSource, DataSourceResult
from app.data_sources.calendar_protocol import CalendarEvent
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.data_sources.google_calendar")


class GoogleCalendarDataSource(DataSource):
    name = "google_calendar"
    description = "Google Calendar events via sibling google-workspace-mcp"
    requires_api_key = True
    api_key_env_var = "CERID_CONNECTORS_BEARER"  # pragma: allowlist secret

    def is_configured(self) -> bool:
        return bool(os.getenv("CERID_CONNECTORS_BEARER")) and bool(
            os.getenv("GOOGLE_OAUTH_CLIENT_ID")
        )

    async def _call_mcp(self, tool_name: str, args: dict[str, Any]) -> Any:
        from core.mcp_clients.client_pool import get_pool

        pool = get_pool()
        return await pool.call_tool("google_workspace", tool_name, args)

    async def list_events(
        self,
        *,
        start: datetime,
        end: datetime,
        max_results: int = 50,
    ) -> list[CalendarEvent]:
        """CalendarDataSource Protocol — used by meeting_capture stitching."""
        try:
            raw = await self._call_mcp(
                "get_events",
                {
                    "time_min": start.isoformat(),
                    "time_max": end.isoformat(),
                    "max_results": max_results,
                },
            )
        except Exception as exc:  # noqa: BLE001 — sibling MCP can fail many ways
            log_swallowed_error("google_calendar.list_events", exc)
            return []
        return _coerce_events(raw)

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        """DataSource fan-out — natural-language calendar query.

        The Google Workspace MCP server exposes ``search_calendar_events``
        or similar tool surfaces (varies by version). We use ``get_events``
        with a default time window (next 30 days) and let the LLM ranker
        post-filter for relevance to the user query.
        """
        from datetime import timedelta, timezone

        now = datetime.now(tz=timezone.utc)
        try:
            raw = await self._call_mcp(
                "get_events",
                {
                    "time_min": now.isoformat(),
                    "time_max": (now + timedelta(days=30)).isoformat(),
                    "max_results": int(kwargs.get("max_results", 25)),
                    "q": query,  # if the server supports keyword filter; ignored otherwise
                },
            )
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("google_calendar.query", exc)
            return []

        events = _coerce_events(raw)
        out: list[DataSourceResult] = []
        for ev in events:
            title = ev.get("title", "(no title)")
            start = ev.get("start")
            end = ev.get("end")
            attendees = ev.get("attendees", [])
            body = (
                f"Title: {title}\n"
                f"Start: {start.isoformat() if isinstance(start, datetime) else start}\n"
                f"End: {end.isoformat() if isinstance(end, datetime) else end}\n"
                f"Attendees: {', '.join(attendees) if attendees else '(none)'}\n"
                f"{ev.get('description', '')}"
            )
            out.append(
                DataSourceResult(
                    title=title,
                    content=body,
                    source_url="https://calendar.google.com/calendar/u/0/r",
                    source_name="Google Calendar",
                    confidence=0.65,
                ),
            )
        return out


def _coerce_events(raw: Any) -> list[CalendarEvent]:
    """Normalize the MCP response shape to a list of CalendarEvent dicts.

    Different google-workspace-mcp versions return events under different
    keys (``events``, ``items``, ``results``, plain list). Each event's
    ``start``/``end`` may be ``{"dateTime": "..."}`` (timed) or
    ``{"date": "..."}`` (all-day).
    """
    events_in: list[dict[str, Any]] = []
    if isinstance(raw, list):
        events_in = [e for e in raw if isinstance(e, dict)]
    elif isinstance(raw, dict):
        for key in ("events", "items", "results", "data"):
            if isinstance(raw.get(key), list):
                events_in = [e for e in raw[key] if isinstance(e, dict)]
                break

    out: list[CalendarEvent] = []
    for e in events_in:
        ev: CalendarEvent = {}
        if (eid := e.get("id")) is not None:
            ev["id"] = str(eid)
        if (title := e.get("summary") or e.get("title")) is not None:
            ev["title"] = str(title)

        start_dt = _parse_event_time(e.get("start"))
        end_dt = _parse_event_time(e.get("end"))
        if start_dt:
            ev["start"] = start_dt
        if end_dt:
            ev["end"] = end_dt

        attendees_raw = e.get("attendees", [])
        if isinstance(attendees_raw, list):
            attendees: list[str] = []
            for a in attendees_raw:
                if isinstance(a, str):
                    attendees.append(a)
                elif isinstance(a, dict):
                    if "email" in a:
                        attendees.append(str(a["email"]))
                    elif "displayName" in a:
                        attendees.append(str(a["displayName"]))
            if attendees:
                ev["attendees"] = attendees

        if (organizer := e.get("organizer")) is not None:
            if isinstance(organizer, dict):
                ev["organizer"] = str(organizer.get("email") or organizer.get("displayName") or "")
            else:
                ev["organizer"] = str(organizer)
        if (desc := e.get("description")) is not None:
            ev["description"] = str(desc)
        if (loc := e.get("location")) is not None:
            ev["location"] = str(loc)

        out.append(ev)
    return out


def _parse_event_time(value: Any) -> datetime | None:
    """Google Calendar events represent timing as either:
      {"dateTime": "2026-05-21T10:00:00-04:00"}    (timed)
      {"date": "2026-05-21"}                       (all-day)
      "2026-05-21T10:00:00-04:00"                  (some MCP servers flatten it)
    """
    if value is None:
        return None
    if isinstance(value, str):
        return _safe_iso(value)
    if isinstance(value, dict):
        dt = value.get("dateTime") or value.get("date")
        if isinstance(dt, str):
            return _safe_iso(dt)
    return None


def _safe_iso(s: str) -> datetime | None:
    try:
        # All-day dates have no T → add midnight UTC
        if "T" not in s:
            s = f"{s}T00:00:00+00:00"
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
