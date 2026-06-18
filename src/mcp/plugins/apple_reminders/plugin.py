# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Apple Reminders connector plugin — Phase 4.3."""
from __future__ import annotations

import logging
from typing import Any

from plugins.base import ConnectorPlugin

from .data_source import AppleRemindersDataSource

logger = logging.getLogger("ai-companion.plugins.apple_reminders")


class AppleRemindersConnectorPlugin(ConnectorPlugin):
    @property
    def name(self) -> str:
        return "apple_reminders"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Apple Reminders via Swift EventKit helper"

    def get_data_source(self) -> Any:
        return AppleRemindersDataSource()

    def register(self) -> None:
        from config.features import is_feature_enabled

        if not is_feature_enabled("reminders_eventkit"):
            logger.info("apple_reminders connector skipped — feature flag off")
            return
        super().register()
        logger.info("apple_reminders connector registered")
