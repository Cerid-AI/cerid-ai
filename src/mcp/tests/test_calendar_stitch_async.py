# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the async match_to_event path (Phase F Day 5)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_match_to_event_with_async_calendar_source(tmp_path, monkeypatch):
    """Verifies the new async path: GoogleCalendarDataSource.list_events
    is awaited (not invoked sync)."""
    from plugins.meeting_capture import calendar_stitch

    # Create a fake audio file so _recording_window doesn't error
    fake_audio = tmp_path / "meeting.wav"
    fake_audio.write_bytes(b"\x00" * 1024)

    # Stub the recording window so we don't depend on ffprobe
    rec_start = datetime(2026, 5, 21, 10, 0, 0, tzinfo=timezone.utc)
    rec_end = datetime(2026, 5, 21, 11, 0, 0, tzinfo=timezone.utc)

    async def _list_events(*, start, end, max_results=50):
        return [
            {
                "id": "evt-1",
                "title": "Daily standup",
                "start": rec_start,
                "end": rec_end,
                "attendees": ["alice@example.com"],
            },
        ]

    fake_source = MagicMock()
    fake_source.list_events = _list_events
    fake_source.name = "google_calendar"

    with (
        patch.object(
            calendar_stitch, "_recording_window",
            return_value=(rec_start, rec_end),
        ),
        patch("app.data_sources.registry.get", return_value=fake_source),
    ):
        result = await calendar_stitch.match_to_event(str(fake_audio), 3600.0)

    assert result is not None
    assert result["calendar_event_id"] == "evt-1"
    assert result["calendar_event_title"] == "Daily standup"
    assert result["attendees"] == ["alice@example.com"]


@pytest.mark.asyncio
async def test_match_to_event_no_calendar_registered(tmp_path):
    from plugins.meeting_capture import calendar_stitch

    fake_audio = tmp_path / "meeting.wav"
    fake_audio.write_bytes(b"\x00" * 1024)

    with patch("app.data_sources.registry.get", return_value=None):
        result = await calendar_stitch.match_to_event(str(fake_audio), 3600.0)

    assert result is None


@pytest.mark.asyncio
async def test_match_to_event_source_missing_list_events(tmp_path):
    """If the registered source doesn't expose list_events, return None
    gracefully rather than crashing."""
    from plugins.meeting_capture import calendar_stitch

    fake_audio = tmp_path / "meeting.wav"
    fake_audio.write_bytes(b"\x00" * 1024)

    bad_source = MagicMock(spec=[])  # no list_events attribute
    bad_source.name = "bogus"

    with patch("app.data_sources.registry.get", return_value=bad_source):
        result = await calendar_stitch.match_to_event(str(fake_audio), 3600.0)

    assert result is None


@pytest.mark.asyncio
async def test_match_to_event_under_threshold_returns_none(tmp_path):
    """Coverage <80% should soft-skip rather than wrongly attribute the event."""
    from plugins.meeting_capture import calendar_stitch

    fake_audio = tmp_path / "meeting.wav"
    fake_audio.write_bytes(b"\x00" * 1024)

    rec_start = datetime(2026, 5, 21, 10, 0, 0, tzinfo=timezone.utc)
    rec_end = datetime(2026, 5, 21, 11, 0, 0, tzinfo=timezone.utc)

    # Event that overlaps only 10 of 60 minutes (16%)
    ev_start = datetime(2026, 5, 21, 10, 50, 0, tzinfo=timezone.utc)
    ev_end = datetime(2026, 5, 21, 11, 30, 0, tzinfo=timezone.utc)

    async def _list_events(**_kwargs):
        return [{"id": "evt-1", "title": "Short event", "start": ev_start, "end": ev_end}]

    fake_source = MagicMock()
    fake_source.list_events = _list_events
    fake_source.name = "google_calendar"

    with (
        patch.object(
            calendar_stitch, "_recording_window",
            return_value=(rec_start, rec_end),
        ),
        patch("app.data_sources.registry.get", return_value=fake_source),
    ):
        result = await calendar_stitch.match_to_event(str(fake_audio), 3600.0)

    assert result is None
