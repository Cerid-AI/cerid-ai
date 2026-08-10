# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Google Calendar DataSource reply parsing.

Like Gmail, ``get_events`` answers in PROSE. The old coercer looked for
``events``/``items``/``results`` keys the server has never emitted, so every
calendar query returned nothing.

The fixtures reproduce the exact strings built by ``gcalendar/calendar_tools.py``
inside the sibling image (read from source on 2026-08-09, because the operator's
calendar is empty — an empty calendar makes a *correct* zero indistinguishable
from the bug, so live data could not have validated this).
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from plugins.google_calendar.data_source import parse_events

DETAILED_REPLY = """Successfully retrieved 2 events from calendar 'primary' for a@example.com:
- "Standup" (Starts: 2026-05-21T10:00:00-04:00, Ends: 2026-05-21T10:15:00-04:00)
  Description: Daily sync
  Location: No Location
  Meeting Link: https://meet.google.com/abc-defg-hij
  Attendees: a@example.com, b@example.com
  Attendee Details: a@example.com (accepted)
  ID: evt_001 | Link: https://www.google.com/calendar/event?eid=001
- "Quarterly review" (Starts: 2026-06-01T09:00:00-04:00, Ends: 2026-06-01T11:00:00-04:00)
  Description: No Description
  Location: Room 4
  Attendees: None
  Attendee Details: None
  ID: evt_002 | Link: https://www.google.com/calendar/event?eid=002"""

EMPTY_REPLY = (
    "No events found in calendar 'primary' for a@example.com "
    "for the specified time range."
)


class TestParseEvents:
    def test_extracts_both_events(self):
        events = parse_events(DETAILED_REPLY)
        assert [e["title"] for e in events] == ["Standup", "Quarterly review"]

    def test_parses_start_and_end_as_datetimes(self):
        first = parse_events(DETAILED_REPLY)[0]
        assert isinstance(first["start"], datetime)
        assert first["start"].year == 2026
        assert first["start"].hour == 10
        assert first["end"].minute == 15

    def test_splits_fields_to_the_right_event(self):
        """Blocks are delimited by the next header, so field values must not
        bleed from one event into the next."""
        first, second = parse_events(DETAILED_REPLY)
        assert first["description"] == "Daily sync"
        assert first["attendees"] == ["a@example.com", "b@example.com"]
        assert second["location"] == "Room 4"
        assert second["id"] == "evt_002"

    def test_placeholder_literals_are_dropped_not_carried(self):
        """The server writes "No Description" / "None" for absent fields.
        Passing those through would put them into an answer as content."""
        first, second = parse_events(DETAILED_REPLY)
        assert "location" not in first          # was "No Location"
        assert "description" not in second      # was "No Description"
        assert "attendees" not in second        # was "None"

    def test_ids_are_captured(self):
        assert parse_events(DETAILED_REPLY)[0]["id"] == "evt_001"

    def test_an_empty_calendar_yields_no_events(self):
        assert parse_events(EMPTY_REPLY) == []

    def test_a_title_containing_a_quote_does_not_swallow_the_timing(self):
        reply = (
            '- "Ops sync (the "real" one)" '
            "(Starts: 2026-05-21T10:00:00-04:00, Ends: 2026-05-21T10:30:00-04:00)\n"
            "  ID: evt_q | Link: https://example.com"
        )
        ev = parse_events(reply)[0]
        assert isinstance(ev["start"], datetime)
        assert ev["id"] == "evt_q"

    def test_all_day_events_parse(self):
        reply = (
            '- "Holiday" (Starts: 2026-07-04, Ends: 2026-07-05)\n'
            "  ID: evt_ad | Link: https://example.com"
        )
        ev = parse_events(reply)[0]
        assert isinstance(ev["start"], datetime)
        assert ev["start"].month == 7

    def test_survives_a_call_tool_result_wrapper(self):
        raw = SimpleNamespace(content=[SimpleNamespace(text=DETAILED_REPLY)])
        assert len(parse_events(raw)) == 2

    def test_unreadable_input_is_empty_not_a_crash(self):
        assert parse_events(None) == []
        assert parse_events(object()) == []
        assert parse_events("") == []
