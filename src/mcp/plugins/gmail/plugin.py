# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""Gmail connector plugin — Phase F Day 3.

ConnectorPlugin subclass that registers ``GmailDataSource`` with the
global registry. The DataSource routes queries through MCPClientPool
to the sibling ``google-workspace-mcp`` container, which owns the
Google OAuth flow + refresh-token rotation.

Activation: Pro tier + ``gmail_connector`` feature flag + OAuth
completed against the sibling MCP server. The plugin self-skips
registration if the feature is off or the bearer token is unset
(operator hasn't run docker compose with --profile pro yet).
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.base import ConnectorPlugin

from .data_source import GmailDataSource

logger = logging.getLogger("ai-companion.plugins.gmail")


class GmailConnectorPlugin(ConnectorPlugin):
    """Pro-tier Gmail connector."""

    @property
    def name(self) -> str:
        return "gmail"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Gmail via sibling google-workspace-mcp container"

    def get_data_source(self) -> Any:
        return GmailDataSource()

    def register(self) -> None:
        from config.features import is_feature_enabled

        if not is_feature_enabled("gmail_connector"):
            logger.info("gmail connector skipped — feature flag off")
            return
        super().register()
        logger.info("gmail connector registered")
