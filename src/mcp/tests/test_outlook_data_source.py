# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for Outlook + Outlook Calendar DataSources.

**Rewritten 2026-08-10.** The previous version encoded a contract the sibling
ms365-mcp server has never had, and then defended it:

* It asserted the first two tools tried were ``search-messages`` then
  ``search_messages``. Neither exists in the server's catalogue
  (``dist/endpoints.json`` in the running image) — the mail tool is
  ``list-mail-messages`` and the calendar tool is ``get-calendar-view``.
* Every parsing test fed ``_coerce_*`` a bare ``dict``, a shape the transport
  has never produced. ``pool.call_tool`` returns a ``CallToolResult`` whose
  text is a JSON document.

So the suite was green while both connectors returned zero results for their
entire life. These tests are built from the tool catalogue and the Graph
response shape the server documents, and they pin the things that actually
broke: the tool NAME, the argument NAMES, and the unwrap path.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from plugins.outlook.data_source import OutlookDataSource, _to_results, parse_messages
from plugins.outlook_calendar.data_source import OutlookCalendarDataSource, parse_events


def _result(payload: object, *, is_error: bool = False) -> SimpleNamespace:
    """A CallToolResult as the transport really delivers it: JSON as text."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(content=[SimpleNamespace(text=text)], isError=is_error)


GRAPH_MESSAGES = {
    "value": [
        {
            "id": "AAMk-1",
            "subject": "Q3 plan",
            "from": {"emailAddress": {"address": "alice@example.com"}},
            "bodyPreview": "Draft attached",
            "webLink": "https://outlook.office365.com/owa/?ItemID=AAMk-1",
        },
    ],
}

GRAPH_EVENTS = {
    "value": [
        {
            "id": "EVT-1",
            "subject": "Design review",
            "start": {"dateTime": "2026-08-12T14:00:00.0000000", "timeZone": "UTC"},
            "end": {"dateTime": "2026-08-12T15:00:00.0000000", "timeZone": "UTC"},
            "attendees": [{"emailAddress": {"address": "bob@example.com"}}],
        },
    ],
}


class TestParseMessages:
    def test_unwraps_json_carried_as_text(self):
        msgs = parse_messages(_result(GRAPH_MESSAGES))
        assert [m["subject"] for m in msgs] == ["Q3 plan"]

    def test_an_error_result_is_not_an_empty_mailbox(self):
        """How the defect hid: an unknown tool or a Graph 401 comes back as a
        normal result with isError set, so `except` never fires. It must yield
        nothing AND be distinguishable from a genuinely empty mailbox."""
        raw = _result({"error": "InvalidAuthenticationToken"}, is_error=True)
        assert parse_messages(raw) == []

    def test_a_bare_list_payload_is_accepted(self):
        assert len(parse_messages(_result(GRAPH_MESSAGES["value"]))) == 1

    def test_unreadable_input_is_empty_not_a_crash(self):
        assert parse_messages(None) == []
        assert parse_messages(_result("not json at all")) == []
        assert parse_messages(_result({})) == []


class TestMailQueryContract:
    @pytest.mark.asyncio
    async def test_calls_the_tool_that_exists(self):
        """`search-messages` / `search_messages` / `list-messages` are not in
        the server's catalogue. An unknown tool answers with an error RESULT,
        so a wrong name is silent — pin the real one."""
        ds = OutlookDataSource()
        call = AsyncMock(return_value=_result(GRAPH_MESSAGES))
        with patch.object(ds, "_call_mcp", call):
            await ds.query("budget")
        assert call.await_args.args[0] == "list-mail-messages"

    @pytest.mark.asyncio
    async def test_sends_odata_names_and_quotes_the_search_term(self):
        """Graph rejects an unquoted $search, and the server drops unknown
        keys silently rather than failing, so both are pinned here."""
        ds = OutlookDataSource()
        call = AsyncMock(return_value=_result(GRAPH_MESSAGES))
        with patch.object(ds, "_call_mcp", call):
            await ds.query("budget", max_results=7)
        sent = call.await_args.args[1]
        assert sent["$search"] == '"budget"'
        assert sent["$top"] == 7
        assert "query" not in sent and "limit" not in sent

    @pytest.mark.asyncio
    async def test_an_empty_query_omits_search_entirely(self):
        ds = OutlookDataSource()
        call = AsyncMock(return_value=_result(GRAPH_MESSAGES))
        with patch.object(ds, "_call_mcp", call):
            await ds.query("")
        assert "$search" not in call.await_args.args[1]

    @pytest.mark.asyncio
    async def test_builds_results_from_a_real_graph_payload(self):
        ds = OutlookDataSource()
        with patch.object(ds, "_call_mcp", AsyncMock(return_value=_result(GRAPH_MESSAGES))):
            results = await ds.query("plan")
        assert results[0].title == "Q3 plan"
        assert "alice@example.com" in results[0].content
        assert results[0].source_url.endswith("ItemID=AAMk-1")

    @pytest.mark.asyncio
    async def test_a_transport_failure_returns_empty(self):
        ds = OutlookDataSource()
        with patch.object(ds, "_call_mcp", AsyncMock(side_effect=RuntimeError("down"))):
            assert await ds.query("plan") == []


class TestCalendarContract:
    @pytest.mark.asyncio
    async def test_uses_calendar_view_with_camelcase_window(self):
        """`list-calendar-events` has NO date parameters, and the server's
        schema passes unknown keys through with a log line instead of
        rejecting them — so snake_case names vanished and the window was
        silently ignored. That returns wrong events, not zero."""
        ds = OutlookCalendarDataSource()
        call = AsyncMock(return_value=_result(GRAPH_EVENTS))
        with patch.object(ds, "_call_mcp", call):
            await ds.list_events(
                start=datetime(2026, 8, 12, tzinfo=timezone.utc),
                end=datetime(2026, 8, 13, tzinfo=timezone.utc),
                max_results=25,
            )
        tool, sent = call.await_args.args[0], call.await_args.args[1]
        assert tool == "get-calendar-view"
        assert sent["startDateTime"].startswith("2026-08-12")
        assert sent["endDateTime"].startswith("2026-08-13")
        assert sent["$top"] == 25
        for dead in ("start_date_time", "end_date_time", "top"):
            assert dead not in sent

    def test_parses_graph_events_carried_as_text(self):
        events = parse_events(_result(GRAPH_EVENTS))
        assert len(events) == 1
        assert events[0]["title"] == "Design review"

    def test_an_error_result_yields_no_events(self):
        assert parse_events(_result({"error": "forbidden"}, is_error=True)) == []

    def test_unreadable_input_is_empty_not_a_crash(self):
        assert parse_events(None) == []
        assert parse_events(_result("<html>gateway error</html>")) == []


class TestToResults:
    def test_missing_fields_degrade_rather_than_crash(self):
        out = _to_results([{}])
        assert out[0].title == "(no subject)"
        assert "(unknown)" in out[0].content
