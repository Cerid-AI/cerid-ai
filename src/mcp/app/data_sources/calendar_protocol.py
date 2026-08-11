# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Calendar DataSource Protocol (Phase F Day 3).

Formalizes the interface that meeting_capture's calendar_stitch.py
expects when calling ``registry.get("google_calendar").list_events()``.
Two implementations land in Phase F:

  * ``GoogleCalendarDataSource`` (this sprint) — routes via the
    google-workspace-mcp sibling container.
  * ``AppleCalendarEventKitDataSource`` (deferred to a Swift-helper
    sprint) — wraps EKEventStore via a native helper.

Phase E's calendar_stitch.py is currently typed as ``Any`` for the
calendar source. With this Protocol in place we can tighten that to
``CalendarDataSource | None`` once the meeting plugin's mypy gate
turns on for the helper line.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, TypedDict, runtime_checkable


class CalendarEvent(TypedDict, total=False):
    id: str
    title: str
    start: datetime  # tz-aware
    end: datetime    # tz-aware
    attendees: list[str]
    organizer: str
    description: str
    location: str
    # Provider deep link to the event itself. Optional: not every provider
    # returns one, and citations fall back to the calendar home when absent.
    html_link: str


@runtime_checkable
class CalendarDataSource(Protocol):
    """Calendar sources must expose this shape so meeting-capture stitching
    can resolve events from any registered provider uniformly."""

    name: str

    async def list_events(
        self,
        *,
        start: datetime,
        end: datetime,
        max_results: int = 50,
    ) -> list[CalendarEvent]:
        """Return events whose [start, end] overlaps the requested window.

        Implementations:
          - MUST return tz-aware datetimes (UTC preferred)
          - SHOULD include attendees when the underlying API surfaces them
          - MAY return empty list on transient errors (caller treats as no-match)
        """
        ...
