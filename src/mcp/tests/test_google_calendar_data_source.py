# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for GoogleCalendarDataSource event coercion (Phase F Day 3)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from plugins.google_calendar.data_source import (
    GoogleCalendarDataSource,
    _coerce_events,
    _parse_event_time,
)


class TestParseEventTime:
    def test_iso_string_round_trip(self):
        s = "2026-05-21T10:00:00+00:00"
        dt = _parse_event_time(s)
        assert isinstance(dt, datetime)
        assert dt.year == 2026
        assert dt.tzinfo is not None

    def test_datetime_wrapped_dict(self):
        dt = _parse_event_time({"dateTime": "2026-05-21T10:00:00-04:00"})
        assert isinstance(dt, datetime)
        assert dt.utcoffset() is not None

    def test_all_day_date(self):
        dt = _parse_event_time({"date": "2026-05-21"})
        assert isinstance(dt, datetime)
        assert dt.hour == 0
        assert dt.tzinfo is not None

    def test_invalid_returns_none(self):
        assert _parse_event_time(None) is None
        assert _parse_event_time({}) is None
        assert _parse_event_time("not a date") is None
        assert _parse_event_time(42) is None

    def test_zulu_suffix(self):
        dt = _parse_event_time("2026-05-21T10:00:00Z")
        assert isinstance(dt, datetime)
        assert dt.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


class TestCoerceEvents:
    def test_empty_input(self):
        assert _coerce_events([]) == []
        assert _coerce_events({}) == []
        assert _coerce_events(None) == []

    def test_google_calendar_shape(self):
        raw = {
            "events": [
                {
                    "id": "evt-1",
                    "summary": "Daily standup",
                    "start": {"dateTime": "2026-05-21T09:00:00+00:00"},
                    "end": {"dateTime": "2026-05-21T09:30:00+00:00"},
                    "attendees": [
                        {"email": "alice@example.com"},
                        {"email": "bob@example.com", "displayName": "Bob"},
                    ],
                    "organizer": {"email": "carol@example.com"},
                    "description": "30m standup",
                },
            ],
        }
        events = _coerce_events(raw)
        assert len(events) == 1
        ev = events[0]
        assert ev["id"] == "evt-1"
        assert ev["title"] == "Daily standup"
        assert ev["attendees"] == ["alice@example.com", "bob@example.com"]
        assert ev["organizer"] == "carol@example.com"
        assert isinstance(ev["start"], datetime)
        assert isinstance(ev["end"], datetime)

    def test_alternate_keys(self):
        # Some MCP versions use 'items' or 'results'
        raw = {"items": [{"id": "x", "summary": "A"}]}
        events = _coerce_events(raw)
        assert len(events) == 1
        assert events[0]["title"] == "A"

    def test_flat_list(self):
        events = _coerce_events([{"id": "x", "summary": "A"}])
        assert len(events) == 1
        assert events[0]["id"] == "x"

    def test_string_attendees(self):
        events = _coerce_events([{"id": "x", "attendees": ["alice@example.com"]}])
        assert events[0]["attendees"] == ["alice@example.com"]

    def test_all_day_event(self):
        events = _coerce_events([
            {"id": "x", "summary": "Holiday", "start": {"date": "2026-12-25"}, "end": {"date": "2026-12-26"}},
        ])
        assert isinstance(events[0]["start"], datetime)
        assert events[0]["start"].hour == 0


class TestDataSourceContract:
    def test_is_configured_requires_bearer_and_client_id(self, monkeypatch):
        ds = GoogleCalendarDataSource()
        monkeypatch.delenv("CERID_CONNECTORS_BEARER", raising=False)
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
        assert ds.is_configured() is False

        monkeypatch.setenv("CERID_CONNECTORS_BEARER", "tok")
        assert ds.is_configured() is False  # need client id too

        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
        assert ds.is_configured() is True

    @pytest.mark.asyncio
    async def test_list_events_fans_to_mcp_pool(self, monkeypatch):
        ds = GoogleCalendarDataSource()

        mock_call = AsyncMock(return_value=[
            {"id": "e1", "summary": "Meeting", "start": {"dateTime": "2026-05-21T10:00:00Z"}, "end": {"dateTime": "2026-05-21T11:00:00Z"}},
        ])
        with patch.object(ds, "_call_mcp", mock_call):
            start = datetime(2026, 5, 21, tzinfo=timezone.utc)
            end = datetime(2026, 5, 22, tzinfo=timezone.utc)
            events = await ds.list_events(start=start, end=end)

        assert len(events) == 1
        assert events[0]["title"] == "Meeting"
        # Confirm we passed the right tool name + ISO window
        mock_call.assert_awaited_once()
        args = mock_call.await_args
        assert args.args[0] == "get_events"
        assert args.args[1]["time_min"].startswith("2026-05-21")
        assert args.args[1]["time_max"].startswith("2026-05-22")

    @pytest.mark.asyncio
    async def test_list_events_returns_empty_on_mcp_failure(self):
        ds = GoogleCalendarDataSource()
        with patch.object(ds, "_call_mcp", AsyncMock(side_effect=RuntimeError("breaker open"))):
            events = await ds.list_events(
                start=datetime(2026, 5, 21, tzinfo=timezone.utc),
                end=datetime(2026, 5, 22, tzinfo=timezone.utc),
            )
        assert events == []
