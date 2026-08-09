# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""Apple Mail connector plugin — Phase 4.1."""
from __future__ import annotations

import logging
from typing import Any

from plugins.base import ConnectorPlugin

from .data_source import AppleMailDataSource

logger = logging.getLogger("ai-companion.plugins.apple_mail")


class AppleMailConnectorPlugin(ConnectorPlugin):
    @property
    def name(self) -> str:
        return "apple_mail"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Apple Mail via signed Swift .emlx-walker helper"

    def get_data_source(self) -> Any:
        return AppleMailDataSource()

    def register(self) -> None:
        from config.features import is_feature_enabled

        if not is_feature_enabled("apple_mail_reader"):
            logger.info("apple_mail connector skipped — feature flag off")
            return
        super().register()
        logger.info("apple_mail connector registered")
