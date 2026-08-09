# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""Outlook Calendar connector plugin — Phase F Day 6."""
from __future__ import annotations

import logging
from typing import Any

from plugins.base import ConnectorPlugin

from .data_source import OutlookCalendarDataSource

logger = logging.getLogger("ai-companion.plugins.outlook_calendar")


class OutlookCalendarConnectorPlugin(ConnectorPlugin):
    @property
    def name(self) -> str:
        return "outlook_calendar"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Outlook Calendar via sibling ms365-mcp container"

    def get_data_source(self) -> Any:
        return OutlookCalendarDataSource()

    def register(self) -> None:
        from config.features import is_feature_enabled

        if not is_feature_enabled("outlook_calendar_sync"):
            logger.info("outlook_calendar connector skipped — feature flag off")
            return
        super().register()
        logger.info("outlook_calendar connector registered")
