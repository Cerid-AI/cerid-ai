# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""Outlook Calendar DataSource — Phase F Day 6.

Implements CalendarDataSource Protocol on top of the sibling ms365-mcp
container. Equivalent role to GoogleCalendarDataSource for the
Microsoft side. The meeting_capture calendar stitching falls back to
this source when google_calendar isn't registered.

The tool is ``get-calendar-view`` (``GET /me/calendarView``, scope
``Calendars.Read``), with **camelCase** ``startDateTime`` / ``endDateTime`` and
``$top`` — read from the running image's ``dist/generated/client.js`` on
2026-08-10.

This was previously ``list-calendar-events`` with ``start_date_time`` /
``end_date_time`` / ``top``. That tool exists but has no date parameters at
all, and the server's argument schema is ``.passthrough()``: unknown keys are
logged as "Dropping unrecognized parameter" and discarded rather than
rejected. The kebab→camel normalizer does not touch snake_case, so the window
could never survive. Masked while parsing was broken; once parsing worked it
would have returned the first N events of the mailbox for EVERY window, and
``calendar_stitch.match_to_event`` would have attributed meeting recordings to
unrelated events — wrong data, which is worse than none. ``list-calendar-events``
also returns only ``seriesMaster`` rows, per its own upstream tip; the
calendarView route expands recurrences, which is what stitching needs.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

from app.data_sources.base import DataSource, DataSourceResult
from app.data_sources.calendar_protocol import CalendarEvent
from core.mcp_clients.result_text import is_error_result, tool_text
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.data_sources.outlook_calendar")


class OutlookCalendarDataSource(DataSource):
    name = "outlook_calendar"
    description = "Outlook Calendar events via sibling ms365-mcp"
    requires_api_key = True
    api_key_env_var = "CERID_CONNECTORS_BEARER"  # pragma: allowlist secret

    def is_configured(self) -> bool:
        return bool(os.getenv("CERID_CONNECTORS_BEARER"))

    async def _call_mcp(self, tool_name: str, args: dict[str, Any]) -> Any:
        from core.mcp_clients.client_pool import get_pool

        pool = get_pool()
        return await pool.call_tool("ms365", tool_name, args)

    async def list_events(
        self,
        *,
        start: datetime,
        end: datetime,
        max_results: int = 50,
    ) -> list[CalendarEvent]:
        try:
            raw = await self._call_mcp(
                "get-calendar-view",
                {
                    "startDateTime": start.isoformat(),
                    "endDateTime": end.isoformat(),
                    "$top": max_results,
                },
            )
        except Exception as exc:  # noqa: BLE001 — sibling MCP can fail many ways
            log_swallowed_error("outlook_calendar.list_events", exc)
            return []
        return parse_events(raw)

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        from datetime import timedelta, timezone

        now = datetime.now(tz=timezone.utc)
        events = await self.list_events(
            start=now,
            end=now + timedelta(days=30),
            max_results=int(kwargs.get("max_results", 25)),
        )
        out: list[DataSourceResult] = []
        for ev in events:
            title = ev.get("title", "(no title)")
            start = ev.get("start")
            end = ev.get("end")
            body = (
                f"Title: {title}\n"
                f"Start: {start.isoformat() if isinstance(start, datetime) else start}\n"
                f"End: {end.isoformat() if isinstance(end, datetime) else end}\n"
                f"{ev.get('description', '')}"
            )
            out.append(
                DataSourceResult(
                    title=title,
                    content=body,
                    source_url="https://outlook.live.com/calendar/0/",
                    source_name="Outlook Calendar",
                    confidence=0.65,
                ),
            )
        return out


def parse_events(raw: Any) -> list[CalendarEvent]:
    """Microsoft Graph event shape:
      { id, subject, start: {dateTime, timeZone}, end: {dateTime, timeZone},
        attendees: [{emailAddress: {address}}], organizer, location, body }

    ``call_tool`` returns a ``CallToolResult`` whose text is a JSON document,
    so the old isinstance-on-``raw`` checks matched nothing and every calendar
    query returned ``[]`` — indistinguishable from a genuinely empty calendar.
    """
    if is_error_result(raw):
        logger.warning(
            "outlook_calendar: tool returned an error result: %s", tool_text(raw)[:200],
        )
        return []
    text = tool_text(raw)
    if not text:
        return []
    try:
        payload = json.loads(text)
    except ValueError as exc:
        log_swallowed_error("outlook_calendar.parse_events", exc)
        return []

    events_in: list[dict[str, Any]] = []
    if isinstance(payload, list):
        events_in = [e for e in payload if isinstance(e, dict)]
    elif isinstance(payload, dict):
        value = payload.get("value")
        if isinstance(value, list):
            events_in = [e for e in value if isinstance(e, dict)]

    out: list[CalendarEvent] = []
    for e in events_in:
        ev: CalendarEvent = {}
        if (eid := e.get("id")) is not None:
            ev["id"] = str(eid)
        if (title := e.get("subject") or e.get("title")) is not None:
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
                if isinstance(a, dict):
                    email = a.get("emailAddress", {})
                    if isinstance(email, dict) and "address" in email:
                        attendees.append(str(email["address"]))
                    elif "address" in a:
                        attendees.append(str(a["address"]))
                elif isinstance(a, str):
                    attendees.append(a)
            if attendees:
                ev["attendees"] = attendees
        body = e.get("body")
        if isinstance(body, dict):
            if (c := body.get("content")) is not None:
                ev["description"] = str(c)
        elif isinstance(body, str):
            ev["description"] = body
        if (loc := e.get("location")) is not None:
            if isinstance(loc, dict):
                ev["location"] = str(loc.get("displayName") or "")
            else:
                ev["location"] = str(loc)
        out.append(ev)
    return out


def _parse_event_time(value: Any) -> datetime | None:
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
        if "T" not in s:
            s = f"{s}T00:00:00+00:00"
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
