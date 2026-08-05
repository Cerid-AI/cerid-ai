# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""Google Calendar connector plugin — Phase F Day 3.

Registers GoogleCalendarDataSource with the data-source registry. The
DataSource conforms to the CalendarDataSource Protocol so the existing
``meeting_capture.calendar_stitch.match_to_event`` lookup
(``registry.get('google_calendar').list_events(...)``) resolves to this
implementation automatically once the connector is enabled.
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.base import ConnectorPlugin

from .data_source import GoogleCalendarDataSource

logger = logging.getLogger("ai-companion.plugins.google_calendar")


class GoogleCalendarConnectorPlugin(ConnectorPlugin):
    @property
    def name(self) -> str:
        return "google_calendar"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Google Calendar via sibling google-workspace-mcp container"

    def get_data_source(self) -> Any:
        return GoogleCalendarDataSource()

    def register(self) -> None:
        from config.features import is_feature_enabled

        if not is_feature_enabled("google_calendar_sync"):
            logger.info("google_calendar connector skipped — feature flag off")
            return
        super().register()
        logger.info("google_calendar connector registered")
