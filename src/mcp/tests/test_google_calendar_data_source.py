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
    def test_is_configured_requires_bearer_client_id_and_account(self, monkeypatch):
        ds = GoogleCalendarDataSource()
        monkeypatch.delenv("CERID_CONNECTORS_BEARER", raising=False)
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("USER_GOOGLE_EMAIL", raising=False)
        assert ds.is_configured() is False

        monkeypatch.setenv("CERID_CONNECTORS_BEARER", "tok")
        assert ds.is_configured() is False  # need client id too

        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
        assert ds.is_configured() is False  # and an account — see gmail test

        monkeypatch.setenv("USER_GOOGLE_EMAIL", "someone@example.com")
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


class TestResultBodyCarriesWhatWasParsed:
    """Found 2026-08-10 by putting the FIRST real event through this path.

    `parse_events` extracted `location` and the per-event link, and `query()`
    dropped both — so "where is my meeting?" answered with nothing while the
    answer sat in the parsed dict, and every citation pointed at the calendar's
    front page. The operator's seeded event had a street address as its only
    substantive field, which is why an empty calendar could never have shown
    this: the parser was right and the consumer was lossy.
    """

    LIVE = (
        "Successfully retrieved 1 events from calendar 'primary' for a@example.com:\n"
        '- "Test Event" (Starts: 2026-08-12T00:30:00-04:00, '
        "Ends: 2026-08-12T01:30:00-04:00)\n"
        "  Description: No Description\n"
        "  Location: HomeGoods, 8357 Leesburg Pike, Vienna, VA 22182, USA\n"
        "  Attendees: None\n"
        "  ID: evt_live | Link: https://www.google.com/calendar/event?eid=abc"
    )

    @pytest.mark.asyncio
    async def test_location_reaches_the_answer(self):
        ds = GoogleCalendarDataSource()
        with patch.object(ds, "_call_mcp", AsyncMock(return_value=self.LIVE)):
            results = await ds.query("test")
        assert "Leesburg Pike" in results[0].content

    @pytest.mark.asyncio
    async def test_a_location_free_event_does_not_emit_an_empty_field(self):
        """The server writes "No Location" for absent values; the parser drops
        it, and the body must not then print `Location: `."""
        ds = GoogleCalendarDataSource()
        reply = self.LIVE.replace(
            "  Location: HomeGoods, 8357 Leesburg Pike, Vienna, VA 22182, USA\n",
            "  Location: No Location\n",
        )
        with patch.object(ds, "_call_mcp", AsyncMock(return_value=reply)):
            results = await ds.query("test")
        assert "Location:" not in results[0].content

    @pytest.mark.asyncio
    async def test_the_citation_deep_links_to_the_event(self):
        ds = GoogleCalendarDataSource()
        with patch.object(ds, "_call_mcp", AsyncMock(return_value=self.LIVE)):
            results = await ds.query("test")
        assert results[0].source_url.endswith("eid=abc")

    @pytest.mark.asyncio
    async def test_it_falls_back_to_the_calendar_home_without_a_link(self):
        ds = GoogleCalendarDataSource()
        reply = self.LIVE.replace(
            " | Link: https://www.google.com/calendar/event?eid=abc", "",
        )
        with patch.object(ds, "_call_mcp", AsyncMock(return_value=reply)):
            results = await ds.query("test")
        assert results[0].source_url.startswith("https://calendar.google.com/")


class TestAccountArgument:
    """``user_google_email`` rides on every sibling call — see the matching
    class in ``test_gmail_data_source.py`` for the full failure history."""

    @pytest.mark.asyncio
    async def test_call_mcp_injects_account(self, monkeypatch):
        monkeypatch.setenv("USER_GOOGLE_EMAIL", "someone@example.com")
        ds = GoogleCalendarDataSource()
        pool = AsyncMock()
        pool.call_tool = AsyncMock(return_value="ok")
        with patch("core.mcp_clients.client_pool.get_pool", return_value=pool):
            await ds._call_mcp("get_events", {"time_min": "2026-01-01T00:00:00Z"})
        sent = pool.call_tool.await_args.args[2]
        assert sent["user_google_email"] == "someone@example.com"
        assert sent["time_min"] == "2026-01-01T00:00:00Z"


class TestAdaptQuery:
    """`get_events` treats `query` as a filter over the time window, so a term
    the events do not contain removes all of them."""

    def test_meta_words_stripped(self):
        ds = GoogleCalendarDataSource()
        out = ds.adapt_query("what meetings do I have with acme", ["meetings", "acme"])
        assert out == "acme"

    def test_meta_only_question_yields_empty_so_the_window_is_returned(self):
        ds = GoogleCalendarDataSource()
        assert ds.adapt_query("what's on my calendar tomorrow", ["calendar", "tomorrow"]) == ""

    @pytest.mark.asyncio
    async def test_empty_query_is_omitted_from_the_call(self):
        """Sending query="" would filter every event out; omitting it returns
        the window, which is what the question actually asked for."""
        ds = GoogleCalendarDataSource()
        mock_call = AsyncMock(return_value=EVENTS_REPLY)
        with patch.object(ds, "_call_mcp", mock_call):
            await ds.query("")
        assert "query" not in mock_call.await_args.args[1]

    @pytest.mark.asyncio
    async def test_real_term_is_still_sent(self):
        ds = GoogleCalendarDataSource()
        mock_call = AsyncMock(return_value=EVENTS_REPLY)
        with patch.object(ds, "_call_mcp", mock_call):
            await ds.query("acme")
        assert mock_call.await_args.args[1]["query"] == "acme"
