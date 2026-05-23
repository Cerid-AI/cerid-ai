# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for Outlook + Outlook Calendar DataSources (Phase F Day 6)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from plugins.outlook.data_source import (
    OutlookDataSource,
    _coerce_messages,
    _to_results,
)
from plugins.outlook_calendar.data_source import (
    OutlookCalendarDataSource,
)
from plugins.outlook_calendar.data_source import (
    _coerce_events as _coerce_calendar_events,
)


class TestOutlookMailCoerce:
    def test_graph_shape(self):
        raw = {
            "value": [
                {
                    "subject": "Q3 plan",
                    "from": {"emailAddress": {"address": "alice@example.com"}},
                    "body": {"content": "see attached"},
                    "webLink": "https://outlook.live.com/m/abc",
                },
            ],
        }
        messages = _coerce_messages(raw)
        assert len(messages) == 1
        results = _to_results(messages)
        assert len(results) == 1
        assert results[0].title == "Q3 plan"
        assert "alice@example.com" in results[0].content
        assert "see attached" in results[0].content
        assert results[0].source_url == "https://outlook.live.com/m/abc"

    def test_alternative_shapes(self):
        # Tool versions can return 'value' / 'messages' / 'results' / flat list
        for shape in (
            {"value": [{"subject": "X"}]},
            {"messages": [{"subject": "X"}]},
            {"results": [{"subject": "X"}]},
            [{"subject": "X"}],
        ):
            messages = _coerce_messages(shape)
            assert len(messages) == 1

    def test_empty_input(self):
        assert _coerce_messages([]) == []
        assert _coerce_messages({}) == []
        assert _coerce_messages(None) == []


class TestOutlookDataSource:
    def test_is_configured_requires_bearer(self, monkeypatch):
        ds = OutlookDataSource()
        monkeypatch.delenv("CERID_CONNECTORS_BEARER", raising=False)
        assert ds.is_configured() is False
        monkeypatch.setenv("CERID_CONNECTORS_BEARER", "tok")
        assert ds.is_configured() is True

    @pytest.mark.asyncio
    async def test_query_tries_multiple_tool_names(self):
        ds = OutlookDataSource()
        calls = []

        async def _stub(tool, args):
            calls.append(tool)
            if tool == "search-messages":
                raise RuntimeError("not in this version")
            if tool == "search_messages":
                return {"value": [{"subject": "Found", "body": {"content": "Hit"}}]}
            return None

        with patch.object(ds, "_call_mcp", AsyncMock(side_effect=_stub)):
            results = await ds.query("test")

        assert len(results) == 1
        assert results[0].title == "Found"
        assert calls[0] == "search-messages"
        assert calls[1] == "search_messages"

    @pytest.mark.asyncio
    async def test_query_returns_empty_when_all_tools_fail(self):
        ds = OutlookDataSource()
        with patch.object(ds, "_call_mcp", AsyncMock(side_effect=RuntimeError("fail"))):
            results = await ds.query("test")
        assert results == []


class TestOutlookCalendarCoerce:
    def test_graph_event_shape(self):
        raw = {
            "value": [
                {
                    "id": "evt-1",
                    "subject": "Sprint planning",
                    "start": {"dateTime": "2026-05-21T10:00:00Z", "timeZone": "UTC"},
                    "end": {"dateTime": "2026-05-21T11:00:00Z", "timeZone": "UTC"},
                    "attendees": [
                        {"emailAddress": {"address": "alice@example.com"}},
                        {"emailAddress": {"address": "bob@example.com"}},
                    ],
                    "body": {"content": "Plan the sprint", "contentType": "text"},
                    "location": {"displayName": "Conference Room A"},
                },
            ],
        }
        events = _coerce_calendar_events(raw)
        assert len(events) == 1
        ev = events[0]
        assert ev["id"] == "evt-1"
        assert ev["title"] == "Sprint planning"
        assert ev["attendees"] == ["alice@example.com", "bob@example.com"]
        assert ev["location"] == "Conference Room A"
        assert isinstance(ev["start"], datetime)


class TestOutlookCalendarDataSource:
    @pytest.mark.asyncio
    async def test_list_events_fans_to_ms365_pool(self):
        ds = OutlookCalendarDataSource()
        mock_call = AsyncMock(return_value={
            "value": [
                {
                    "id": "e1",
                    "subject": "Sync",
                    "start": {"dateTime": "2026-05-21T10:00:00Z"},
                    "end": {"dateTime": "2026-05-21T11:00:00Z"},
                },
            ],
        })
        with patch.object(ds, "_call_mcp", mock_call):
            events = await ds.list_events(
                start=datetime(2026, 5, 21, tzinfo=timezone.utc),
                end=datetime(2026, 5, 22, tzinfo=timezone.utc),
            )
        assert len(events) == 1
        assert events[0]["title"] == "Sync"
        # First tried tool name should be 'list-calendar-events'
        first_call = mock_call.await_args_list[0]
        assert first_call.args[0] == "list-calendar-events"

    @pytest.mark.asyncio
    async def test_list_events_empty_on_all_failure(self):
        ds = OutlookCalendarDataSource()
        with patch.object(ds, "_call_mcp", AsyncMock(side_effect=RuntimeError("breaker"))):
            events = await ds.list_events(
                start=datetime(2026, 5, 21, tzinfo=timezone.utc),
                end=datetime(2026, 5, 22, tzinfo=timezone.utc),
            )
        assert events == []
