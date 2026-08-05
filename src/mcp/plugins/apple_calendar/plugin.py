# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""Apple Calendar connector plugin — Phase G.4."""
from __future__ import annotations

import logging
from typing import Any

from plugins.base import ConnectorPlugin

from .data_source import AppleCalendarDataSource

logger = logging.getLogger("ai-companion.plugins.apple_calendar")


class AppleCalendarConnectorPlugin(ConnectorPlugin):
    @property
    def name(self) -> str:
        return "apple_calendar"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Apple Calendar via Swift EventKit helper"

    def get_data_source(self) -> Any:
        return AppleCalendarDataSource()

    def register(self) -> None:
        from config.features import is_feature_enabled

        # Existing flag in config/features.py — preserves the contract
        # that meeting_capture's calendar_stitch.py expects.
        if not is_feature_enabled("apple_calendar_eventkit"):
            logger.info("apple_calendar connector skipped — feature flag off")
            return
        super().register()
        logger.info("apple_calendar connector registered")
