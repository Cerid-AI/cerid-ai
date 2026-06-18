# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Apple iMessage connector plugin — Phase 4.2."""
from __future__ import annotations

import logging
from typing import Any

from plugins.base import ConnectorPlugin

from .data_source import AppleIMessageDataSource

logger = logging.getLogger("ai-companion.plugins.apple_imessage")


class AppleIMessageConnectorPlugin(ConnectorPlugin):
    @property
    def name(self) -> str:
        return "apple_imessage"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Apple iMessage via signed Swift chat.db reader (private_mode L2+)"

    def get_data_source(self) -> Any:
        return AppleIMessageDataSource()

    def register(self) -> None:
        from config.features import is_feature_enabled

        if not is_feature_enabled("imessage_reader"):
            logger.info("apple_imessage connector skipped — feature flag off")
            return
        super().register()
        logger.info("apple_imessage connector registered")
