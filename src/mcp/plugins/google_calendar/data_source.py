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
import re
from datetime import datetime
from typing import Any

from app.data_sources.base import DataSource, DataSourceResult
from app.data_sources.calendar_protocol import CalendarEvent
from core.mcp_clients.result_text import tool_text
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
                    # Without this the server emits a one-line summary per
                    # event with no description, location or attendees — the
                    # fields meeting_capture stitching actually matches on.
                    "detailed": True,
                },
            )
        except Exception as exc:  # noqa: BLE001 — sibling MCP can fail many ways
            log_swallowed_error("google_calendar.list_events", exc)
            return []
        return parse_events(raw)

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
                    # The parameter is `query`, not `q`. Unknown keywords are
                    # not "ignored otherwise" as the old comment assumed — the
                    # server validates with pydantic and rejects the whole
                    # call ("q Unexpected keyword argument"), returning that
                    # error as a tool RESULT. Every calendar query failed this
                    # way and reported zero events.
                    "query": query,
                    "detailed": True,
                },
            )
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("google_calendar.query", exc)
            return []

        events = parse_events(raw)
        out: list[DataSourceResult] = []
        for ev in events:
            title = ev.get("title", "(no title)")
            start = ev.get("start")
            end = ev.get("end")
            attendees = ev.get("attendees", [])
            # Location was parsed and then dropped. The first real event ever
            # put through this path (2026-08-10) had a street address as its
            # ONLY substantive field, so "where is my meeting?" answered with
            # nothing while the answer sat in the parsed dict.
            location = ev.get("location")
            body = (
                f"Title: {title}\n"
                f"Start: {start.isoformat() if isinstance(start, datetime) else start}\n"
                f"End: {end.isoformat() if isinstance(end, datetime) else end}\n"
                + (f"Location: {location}\n" if location else "")
                + f"Attendees: {', '.join(attendees) if attendees else '(none)'}\n"
                f"{ev.get('description', '')}"
            )
            # Deep-link to the event when the reply carried one, so a citation
            # lands on the event rather than the calendar's front page.
            link = ev.get("html_link")
            out.append(
                DataSourceResult(
                    title=title,
                    content=body,
                    source_url=link or "https://calendar.google.com/calendar/u/0/r",
                    source_name="Google Calendar",
                    confidence=0.65,
                ),
            )
        return out


# ``get_events`` returns PROSE, not JSON — see the module docstring. With
# detailed=True the server emits one block per event
# (gcalendar/calendar_tools.py in the sibling image):
#
#     - "Standup" (Starts: 2026-05-21T10:00:00-04:00, Ends: 2026-05-21T10:15:00-04:00)
#       Description: No Description
#       Location: No Location
#       Meeting Link: https://meet.google.com/abc      <- only when present
#       Attendees: a@example.com, b@example.com
#       Attendee Details: ...
#       ID: 6f1c… | Link: https://www.google.com/calendar/event?eid=…
#
# The header is matched non-greedily up to the literal ``" (Starts:`` so a
# title containing a quote cannot swallow the timing fields.
_EVENT_HEADER_RE = re.compile(
    r'^-\s+"(?P<title>.*?)"\s+\(Starts:\s*(?P<start>[^,]+),\s*Ends:\s*(?P<end>[^)]+)\)',
    re.MULTILINE,
)
_FIELD_RES = {
    "description": re.compile(r"^\s+Description:\s*(.*)$", re.MULTILINE),
    "location": re.compile(r"^\s+Location:\s*(.*)$", re.MULTILINE),
    "attendees": re.compile(r"^\s+Attendees:\s*(.*)$", re.MULTILINE),
}
_ID_RE = re.compile(r"(?:^\s+|\s)ID:\s*(?P<id>\S+)\s*\|\s*Link:\s*(?P<link>\S+)", re.MULTILINE)

# The server writes these literals when a field is absent. Carrying them
# through would put "No Description" in an answer as though it were content.
_ABSENT = {"No Description", "No Location", "No Title", "None", "No Link", "No ID"}


def _clean(value: str) -> str:
    value = value.strip()
    return "" if value in _ABSENT else value


def parse_events(raw: Any) -> list[CalendarEvent]:
    """Parse a ``get_events`` reply into CalendarEvent dicts.

    Replaces a shape-guessing coercer that looked for ``events``/``items``/
    ``results`` keys. The server has never returned any of those: it returns
    text, and ``pool.call_tool`` wraps it in a ``CallToolResult``, so the old
    isinstance checks matched nothing and every calendar query came back
    empty.
    """
    text = tool_text(raw)
    if not text:
        return []

    headers = list(_EVENT_HEADER_RE.finditer(text))
    out: list[CalendarEvent] = []
    for i, match in enumerate(headers):
        # Body of this event = up to the next event header (or end of text).
        block_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[match.end():block_end]

        ev: CalendarEvent = {}
        if title := _clean(match.group("title")):
            ev["title"] = title
        if start_dt := _safe_iso(match.group("start").strip()):
            ev["start"] = start_dt
        if end_dt := _safe_iso(match.group("end").strip()):
            ev["end"] = end_dt

        for field, pattern in _FIELD_RES.items():
            found = pattern.search(block)
            if not found:
                continue
            value = _clean(found.group(1))
            if not value:
                continue
            if field == "attendees":
                ev["attendees"] = [a.strip() for a in value.split(",") if a.strip()]
            else:
                ev[field] = value  # type: ignore[literal-required]

        if id_match := _ID_RE.search(block):
            if eid := _clean(id_match.group("id")):
                ev["id"] = eid
            # The reply carries a per-event Link on the same line; it was
            # matched and then thrown away, so every citation pointed at the
            # calendar's front page instead of the event.
            if elink := _clean(id_match.group("link")):
                ev["html_link"] = elink

        out.append(ev)
    return out


def _safe_iso(s: str) -> datetime | None:
    try:
        # All-day dates have no T → add midnight UTC
        if "T" not in s:
            s = f"{s}T00:00:00+00:00"
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
