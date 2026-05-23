# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Outlook connector plugin — Phase F Day 6.

Routes Outlook Mail queries to the sibling ms365-mcp container (Softeria)
via the MCPClientPool's 'ms365' connector. MSAL device-code OAuth is
owned by the server.
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.base import ConnectorPlugin

from .data_source import OutlookDataSource

logger = logging.getLogger("ai-companion.plugins.outlook")


class OutlookConnectorPlugin(ConnectorPlugin):
    @property
    def name(self) -> str:
        return "outlook"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Outlook Mail via sibling ms365-mcp container"

    def get_data_source(self) -> Any:
        return OutlookDataSource()

    def register(self) -> None:
        from config.features import is_feature_enabled

        if not is_feature_enabled("outlook_connector"):
            logger.info("outlook connector skipped — feature flag off")
            return
        super().register()
        logger.info("outlook connector registered")
