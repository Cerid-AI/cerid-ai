# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for AppleCalendarDataSource — Phase G.4.

The Swift helper isn't built or invoked in the test env (CI may not
even be on macOS). All subprocess calls are stubbed; we verify the
JSON parsing + Protocol conformance + error handling.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.apple_calendar.data_source import AppleCalendarDataSource


@pytest.fixture
def helper_path(tmp_path):
    """Returns a path that exists (so is_configured() passes), even
    though we never actually exec it."""
    p = tmp_path / "ceridek"
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(0o755)
    return str(p)


class TestConfiguration:
    def test_is_configured_requires_darwin(self, helper_path):
        with patch("platform.system", return_value="Linux"):
            ds = AppleCalendarDataSource(helper_path=helper_path)
            assert ds.is_configured() is False

    def test_is_configured_requires_helper_present(self):
        with patch("platform.system", return_value="Darwin"):
            ds = AppleCalendarDataSource(helper_path=None)
            assert ds.is_configured() is False

    def test_is_configured_when_both_present(self, helper_path):
        with patch("platform.system", return_value="Darwin"):
            ds = AppleCalendarDataSource(helper_path=helper_path)
            assert ds.is_configured() is True


def _make_proc_mock(stdout_bytes: bytes, returncode: int = 0, stderr_bytes: bytes = b""):
    """Build the async-subprocess mock chain we need."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout_bytes, stderr_bytes))
    return AsyncMock(return_value=proc)


class TestListEvents:
    @pytest.mark.asyncio
    async def test_parses_helper_json(self, helper_path):
        ds = AppleCalendarDataSource(helper_path=helper_path)
        payload = [
            {
                "id": "evt-1",
                "title": "Daily standup",
                "start": "2026-05-21T09:00:00+00:00",
                "end": "2026-05-21T09:30:00+00:00",
                "attendees": ["alice@example.com", "bob@example.com"],
                "organizer": "carol@example.com",
                "calendar": "Work",
            },
        ]
        proc_factory = _make_proc_mock(json.dumps(payload).encode("utf-8"))
        with patch("asyncio.create_subprocess_exec", proc_factory):
            events = await ds.list_events(
                start=datetime(2026, 5, 21, tzinfo=timezone.utc),
                end=datetime(2026, 5, 22, tzinfo=timezone.utc),
            )
        assert len(events) == 1
        ev = events[0]
        assert ev["id"] == "evt-1"
        assert ev["title"] == "Daily standup"
        assert ev["attendees"] == ["alice@example.com", "bob@example.com"]
        assert isinstance(ev["start"], datetime)

    @pytest.mark.asyncio
    async def test_tcc_denial_returns_empty(self, helper_path):
        ds = AppleCalendarDataSource(helper_path=helper_path)
        # Exit code 3 from the Swift helper means TCC denied
        proc_factory = _make_proc_mock(b"", returncode=3, stderr_bytes=b"access denied")
        with patch("asyncio.create_subprocess_exec", proc_factory):
            events = await ds.list_events(
                start=datetime(2026, 5, 21, tzinfo=timezone.utc),
                end=datetime(2026, 5, 22, tzinfo=timezone.utc),
            )
        assert events == []

    @pytest.mark.asyncio
    async def test_helper_crash_returns_empty(self, helper_path):
        ds = AppleCalendarDataSource(helper_path=helper_path)
        proc_factory = _make_proc_mock(b"", returncode=1, stderr_bytes=b"crash")
        with patch("asyncio.create_subprocess_exec", proc_factory):
            events = await ds.list_events(
                start=datetime(2026, 5, 21, tzinfo=timezone.utc),
                end=datetime(2026, 5, 22, tzinfo=timezone.utc),
            )
        assert events == []

    @pytest.mark.asyncio
    async def test_no_helper_path_returns_empty(self):
        ds = AppleCalendarDataSource(helper_path=None)
        events = await ds.list_events(
            start=datetime(2026, 5, 21, tzinfo=timezone.utc),
            end=datetime(2026, 5, 22, tzinfo=timezone.utc),
        )
        assert events == []

    @pytest.mark.asyncio
    async def test_respects_max_results(self, helper_path):
        ds = AppleCalendarDataSource(helper_path=helper_path)
        payload = [
            {"id": f"evt-{i}", "title": f"Event {i}",
             "start": "2026-05-21T09:00:00+00:00",
             "end": "2026-05-21T10:00:00+00:00"}
            for i in range(100)
        ]
        proc_factory = _make_proc_mock(json.dumps(payload).encode("utf-8"))
        with patch("asyncio.create_subprocess_exec", proc_factory):
            events = await ds.list_events(
                start=datetime(2026, 5, 21, tzinfo=timezone.utc),
                end=datetime(2026, 5, 22, tzinfo=timezone.utc),
                max_results=10,
            )
        assert len(events) == 10


class TestQuery:
    @pytest.mark.asyncio
    async def test_query_returns_results(self, helper_path):
        ds = AppleCalendarDataSource(helper_path=helper_path)
        payload = [
            {
                "id": "evt-1",
                "title": "Sprint planning",
                "start": "2026-05-21T10:00:00+00:00",
                "end": "2026-05-21T11:00:00+00:00",
                "attendees": ["alice@example.com"],
                "description": "Plan the sprint",
            },
        ]
        proc_factory = _make_proc_mock(json.dumps(payload).encode("utf-8"))
        with patch("asyncio.create_subprocess_exec", proc_factory):
            results = await ds.query("any text")
        assert len(results) == 1
        assert results[0].title == "Sprint planning"
        assert "alice@example.com" in results[0].content
        assert results[0].source_name == "Apple Calendar"
