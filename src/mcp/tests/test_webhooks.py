# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for webhook notifications (Phase 4C.4)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.webhooks import fire_event


@pytest.mark.asyncio
async def test_fire_event_no_hooks():
    """When no webhooks configured, should return 0."""
    with patch("utils.webhooks.config") as mock_config:
        mock_config.WEBHOOK_ENDPOINTS = []
        result = await fire_event("test.event", {"key": "value"})
        assert result == 0


@pytest.mark.asyncio
async def test_fire_event_filters_by_type():
    """Webhooks with event filters should only fire for matching events."""
    with patch("utils.webhooks.config") as mock_config, \
         patch("utils.webhooks.httpx.AsyncClient") as mock_client_cls:
        mock_config.WEBHOOK_ENDPOINTS = [
            {"url": "http://example.com/hook", "events": ["ingestion.complete"]},
        ]
        # Create a mock response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        # This event type doesn't match the filter
        result = await fire_event("health.warning", {"status": "degraded"})
        assert result == 0
        mock_client.post.assert_not_called()
