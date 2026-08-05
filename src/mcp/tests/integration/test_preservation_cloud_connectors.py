# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Cloud-connector preservation invariants — Phase F Day 7.

Verifies the contract surfaces Phase F depends on remain stable:

  * Feature flags pre-declared in config/features.py for all 4 connectors
  * CalendarDataSource Protocol shape is importable and intact
  * Plugin module paths resolve (plugins/gmail, /google_calendar,
    /outlook, /outlook_calendar)
  * MCPClientPool exposes the per-connector headers contract
  * Settings exposes the four env vars Phase F introduced
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.preservation


def test_feature_flags_declared():
    """All four Pro cloud connector flags must exist in the registry —
    the connector plugins skip self-registration if their flag is off,
    so a missing flag would silently disable the connector."""
    from config.features import FEATURE_FLAGS

    assert "gmail_connector" in FEATURE_FLAGS
    assert "outlook_connector" in FEATURE_FLAGS
    assert "google_calendar_sync" in FEATURE_FLAGS
    assert "outlook_calendar_sync" in FEATURE_FLAGS


def test_calendar_protocol_is_importable():
    from app.data_sources.calendar_protocol import CalendarDataSource, CalendarEvent
    assert hasattr(CalendarDataSource, "list_events")
    # CalendarEvent is a TypedDict — Python treats TypedDict as dict at runtime,
    # so we just verify the symbol resolves and is callable as expected.
    assert callable(CalendarEvent) or isinstance(CalendarEvent, type)


def test_plugin_modules_importable():
    """All 4 connector plugin modules must be importable. Their register()
    method gates on feature flags so importing is safe even when disabled."""
    from plugins.gmail.data_source import GmailDataSource
    from plugins.gmail.plugin import GmailConnectorPlugin
    from plugins.google_calendar.data_source import GoogleCalendarDataSource
    from plugins.google_calendar.plugin import GoogleCalendarConnectorPlugin
    from plugins.outlook.data_source import OutlookDataSource
    from plugins.outlook.plugin import OutlookConnectorPlugin
    from plugins.outlook_calendar.data_source import OutlookCalendarDataSource
    from plugins.outlook_calendar.plugin import OutlookCalendarConnectorPlugin

    assert GmailConnectorPlugin().name == "gmail"
    assert GoogleCalendarConnectorPlugin().name == "google_calendar"
    assert OutlookConnectorPlugin().name == "outlook"
    assert OutlookCalendarConnectorPlugin().name == "outlook_calendar"

    assert GmailDataSource.name == "gmail"
    assert GoogleCalendarDataSource.name == "google_calendar"
    assert OutlookDataSource.name == "outlook"
    assert OutlookCalendarDataSource.name == "outlook_calendar"


def test_mcp_client_pool_accepts_headers():
    """register(headers=...) is the per-connector auth path. If this
    signature drifts, the lifespan hook in app/main.py will start
    silently failing to attach the bearer to outbound requests."""
    import inspect

    from core.mcp_clients.client_pool import MCPClientPool

    sig = inspect.signature(MCPClientPool.register)
    assert "headers" in sig.parameters


def test_settings_exposes_phase_f_env_vars():
    from config import settings

    assert hasattr(settings, "CERID_CONNECTORS_BEARER")
    assert hasattr(settings, "GOOGLE_WORKSPACE_MCP_URL")
    assert hasattr(settings, "MS365_MCP_URL")
    assert hasattr(settings, "GOOGLE_OAUTH_CLIENT_ID")
    # Defaults point at container DNS names
    assert "google-workspace-mcp" in settings.GOOGLE_WORKSPACE_MCP_URL
    assert "ms365-mcp" in settings.MS365_MCP_URL


def test_calendar_stitch_fallback_chain_includes_outlook():
    """match_to_event must check google_calendar → outlook_calendar →
    apple_calendar_eventkit in that order. A regression in the fallback
    chain would silently disable Outlook calendar stitching."""
    import inspect

    from plugins.meeting_capture import calendar_stitch

    source = inspect.getsource(calendar_stitch.match_to_event)
    # Crude but stable — check the calendar registry lookups by name
    assert 'registry.get("google_calendar")' in source
    assert 'registry.get("outlook_calendar")' in source
    # Phase G.4 added the Swift-helper-backed apple_calendar; the legacy
    # apple_calendar_eventkit key remains as a tail fallback.
    assert 'registry.get("apple_calendar")' in source
    assert 'registry.get("apple_calendar_eventkit")' in source
