# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for webhook notifications."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.webhooks import fire_event


def _no_subscriptions() -> MagicMock:
    """A get_redis() stand-in with no CRUD-registered subscriptions."""
    redis_client = MagicMock()
    redis_client.keys.return_value = []
    return redis_client


@pytest.mark.asyncio
async def test_fire_event_no_hooks(monkeypatch):
    """When no webhooks configured, should return 0."""
    monkeypatch.setattr("config.WEBHOOK_ENDPOINTS", [])
    with patch("utils.webhooks.get_redis", return_value=_no_subscriptions()):
        result = await fire_event("test.event", {"key": "value"})
        assert result == 0


@pytest.mark.asyncio
async def test_fire_event_filters_by_type(monkeypatch):
    """Webhooks with event filters should only fire for matching events."""
    monkeypatch.setattr("config.WEBHOOK_ENDPOINTS", [
        {"url": "http://example.com/hook", "events": ["ingestion.complete"]},
    ])
    with patch("utils.webhooks.get_redis", return_value=_no_subscriptions()), \
         patch("utils.webhooks.httpx.AsyncClient") as mock_client_cls:
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


@pytest.mark.asyncio
async def test_fire_event_delivers_to_registered_subscriptions(monkeypatch):
    """CRUD-registered subscriptions (cerid:webhooks:sub:*) must actually
    receive deliveries, not just the env-var-derived WEBHOOK_ENDPOINTS list."""
    sub = {
        "id": "sub-1",
        "url": "https://example.com/crud-hook",
        "events": [],
        "secret": "my-secret",  # pragma: allowlist secret
        "active": True,
    }
    redis_client = MagicMock()
    redis_client.keys.return_value = ["cerid:webhooks:sub:sub-1"]
    redis_client.get.return_value = json.dumps(sub)

    monkeypatch.setattr("config.WEBHOOK_ENDPOINTS", [])

    # Patch DNS resolution to a known-public address so the SSRF guard
    # (app.reliability.url_safety.is_private_host) doesn't depend on
    # real network access in the test sandbox.
    fake_public = [(2, 1, 6, "", ("93.184.216.34", 0))]
    with patch("utils.webhooks.get_redis", return_value=redis_client), \
         patch("socket.getaddrinfo", return_value=fake_public), \
         patch("utils.webhooks.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await fire_event("ingestion.complete", {"artifact_id": "a1"})

        assert result == 1
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args.args[0] == "https://example.com/crud-hook"
        assert "X-Cerid-Signature" in call_args.kwargs["headers"]


@pytest.mark.asyncio
async def test_fire_event_records_delivery_history_for_subscription(monkeypatch):
    """A successful delivery to a CRUD-registered subscription must be
    recorded to cerid:webhooks:deliveries:{id} so GET /webhooks/{id}/deliveries
    (webhook_subscriptions.py) is not permanently empty."""
    sub = {
        "id": "sub-1",
        "url": "https://example.com/crud-hook",
        "events": [],
        "secret": "",
        "active": True,
    }
    redis_client = MagicMock()
    redis_client.keys.return_value = ["cerid:webhooks:sub:sub-1"]
    redis_client.get.return_value = json.dumps(sub)

    monkeypatch.setattr("config.WEBHOOK_ENDPOINTS", [])

    fake_public = [(2, 1, 6, "", ("93.184.216.34", 0))]
    with patch("utils.webhooks.get_redis", return_value=redis_client), \
         patch("socket.getaddrinfo", return_value=fake_public), \
         patch("utils.webhooks.httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await fire_event("ingestion.complete", {"artifact_id": "a1"})

        assert result == 1
        redis_client.lpush.assert_called_once()
        lpush_key, lpush_value = redis_client.lpush.call_args.args
        assert lpush_key == "cerid:webhooks:deliveries:sub-1"
        record = json.loads(lpush_value)
        assert record["event"] == "ingestion.complete"
        assert record["status_code"] == 200
        assert record["payload"] == {"artifact_id": "a1"}
        redis_client.ltrim.assert_called_once_with("cerid:webhooks:deliveries:sub-1", 0, 99)


@pytest.mark.asyncio
async def test_fire_event_records_delivery_failure_with_error(monkeypatch):
    """A failed delivery attempt must still be recorded, with status_code 0
    and the exception text, so the delivery history explains why nothing
    arrived instead of staying silently empty."""
    sub = {
        "id": "sub-1",
        "url": "https://example.com/crud-hook",
        "events": [],
        "secret": "",
        "active": True,
    }
    redis_client = MagicMock()
    redis_client.keys.return_value = ["cerid:webhooks:sub:sub-1"]
    redis_client.get.return_value = json.dumps(sub)

    monkeypatch.setattr("config.WEBHOOK_ENDPOINTS", [])

    fake_public = [(2, 1, 6, "", ("93.184.216.34", 0))]
    with patch("utils.webhooks.get_redis", return_value=redis_client), \
         patch("socket.getaddrinfo", return_value=fake_public), \
         patch("utils.webhooks.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = OSError("connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await fire_event("ingestion.complete", {"artifact_id": "a1"})

        assert result == 0
        redis_client.lpush.assert_called_once()
        lpush_key, lpush_value = redis_client.lpush.call_args.args
        assert lpush_key == "cerid:webhooks:deliveries:sub-1"
        record = json.loads(lpush_value)
        assert record["status_code"] == 0
        assert "connection refused" in record["error"]


@pytest.mark.asyncio
async def test_fire_event_skips_inactive_subscriptions(monkeypatch):
    """An inactive subscription must not receive deliveries."""
    sub = {"id": "sub-2", "url": "https://example.com/off", "active": False}
    redis_client = MagicMock()
    redis_client.keys.return_value = ["cerid:webhooks:sub:sub-2"]
    redis_client.get.return_value = json.dumps(sub)

    monkeypatch.setattr("config.WEBHOOK_ENDPOINTS", [])

    with patch("utils.webhooks.get_redis", return_value=redis_client), \
         patch("utils.webhooks.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await fire_event("ingestion.complete", {"artifact_id": "a1"})
        assert result == 0
        mock_client.post.assert_not_called()
