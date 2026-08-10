# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for GoogleCalendarDataSource (Phase F Day 3).

**Rewritten 2026-08-09**, for the same reason as ``test_gmail_data_source.py``:
every stub here fed the DataSource fabricated Google-API JSON
(``{"items": [{"summary": ..., "start": {"dateTime": ...}}]}``) which the
sibling MCP server has never emitted. It returns prose. The suite therefore
passed while every real calendar query returned zero events, and it actively
defended the bug — ``test_google_calendar_shape`` asserted the imagined shape
was handled correctly.

Reply parsing is covered in ``test_google_calendar_parsing.py`` against the
server's real output; this file covers the orchestration and the argument
contract.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from plugins.google_calendar.data_source import GoogleCalendarDataSource

EVENTS_REPLY = """Successfully retrieved 1 events from calendar 'primary' for a@example.com:
- "Meeting" (Starts: 2026-05-21T10:00:00+00:00, Ends: 2026-05-21T11:00:00+00:00)
  Description: Weekly sync
  Location: No Location
  Attendees: a@example.com, b@example.com
  Attendee Details: a@example.com (accepted)
  ID: e1 | Link: https://www.google.com/calendar/event?eid=e1"""

EMPTY_REPLY = (
    "No events found in calendar 'primary' for a@example.com "
    "for the specified time range."
)


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
    async def test_list_events_fans_to_mcp_pool(self):
        ds = GoogleCalendarDataSource()
        mock_call = AsyncMock(return_value=EVENTS_REPLY)

        with patch.object(ds, "_call_mcp", mock_call):
            events = await ds.list_events(
                start=datetime(2026, 5, 21, tzinfo=timezone.utc),
                end=datetime(2026, 5, 22, tzinfo=timezone.utc),
            )

        assert len(events) == 1
        assert events[0]["title"] == "Meeting"
        assert events[0]["attendees"] == ["a@example.com", "b@example.com"]
        args = mock_call.await_args
        assert args.args[0] == "get_events"
        assert args.args[1]["time_min"].startswith("2026-05-21")
        assert args.args[1]["time_max"].startswith("2026-05-22")

    @pytest.mark.asyncio
    async def test_list_events_requests_detailed_output(self):
        """Without detailed=True the server emits a one-line summary per event
        with no description, location or attendees — the fields meeting_capture
        stitching matches on."""
        ds = GoogleCalendarDataSource()
        mock_call = AsyncMock(return_value=EVENTS_REPLY)
        with patch.object(ds, "_call_mcp", mock_call):
            await ds.list_events(
                start=datetime(2026, 5, 21, tzinfo=timezone.utc),
                end=datetime(2026, 5, 22, tzinfo=timezone.utc),
            )
        assert mock_call.await_args.args[1]["detailed"] is True

    @pytest.mark.asyncio
    async def test_query_sends_query_not_q(self):
        """`q` is rejected by the server's pydantic validation, and the error
        comes back as a tool RESULT — silent. Pin the parameter name."""
        ds = GoogleCalendarDataSource()
        mock_call = AsyncMock(return_value=EVENTS_REPLY)
        with patch.object(ds, "_call_mcp", mock_call):
            await ds.query("standup")
        sent = mock_call.await_args.args[1]
        assert sent["query"] == "standup"
        assert "q" not in sent

    @pytest.mark.asyncio
    async def test_query_builds_results_from_the_prose_reply(self):
        ds = GoogleCalendarDataSource()
        with patch.object(ds, "_call_mcp", AsyncMock(return_value=EVENTS_REPLY)):
            results = await ds.query("sync")

        assert len(results) == 1
        assert results[0].title == "Meeting"
        assert "a@example.com" in results[0].content
        assert results[0].source_name == "Google Calendar"

    @pytest.mark.asyncio
    async def test_an_empty_calendar_returns_no_results(self):
        ds = GoogleCalendarDataSource()
        with patch.object(ds, "_call_mcp", AsyncMock(return_value=EMPTY_REPLY)):
            assert await ds.query("anything") == []

    @pytest.mark.asyncio
    async def test_list_events_returns_empty_on_mcp_failure(self):
        ds = GoogleCalendarDataSource()
        with patch.object(ds, "_call_mcp", AsyncMock(side_effect=RuntimeError("breaker open"))):
            events = await ds.list_events(
                start=datetime(2026, 5, 21, tzinfo=timezone.utc),
                end=datetime(2026, 5, 22, tzinfo=timezone.utc),
            )
        assert events == []
