# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests: guard_or_log is wired at every outbound-fetch call site."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestWebhooksSSRFGuard:
    @pytest.mark.asyncio
    async def test_webhooks_blocks_private_url(self, monkeypatch):
        """Webhook fire against 127.0.0.1 is blocked — httpx.AsyncClient.post never called."""
        import utils.webhooks as wh

        # Patch config.WEBHOOK_ENDPOINTS to inject a private-host webhook
        mock_config = MagicMock()
        mock_config.WEBHOOK_ENDPOINTS = [{"url": "http://127.0.0.1/hook", "events": None}]
        monkeypatch.setattr(wh, "config", mock_config)

        mock_post = AsyncMock(return_value=MagicMock(status_code=200))

        # Patch sentry to avoid real calls
        monkeypatch.setattr("sentry_sdk.capture_message", lambda *a, **k: None)

        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 0))]):
            with patch("httpx.AsyncClient.post", mock_post):
                delivered = await wh.fire_event("test.event", {"x": 1})

        assert mock_post.call_count == 0, "httpx.post must never be called for a blocked URL"
        assert delivered == 0

    @pytest.mark.asyncio
    async def test_webhooks_allows_public_url(self, monkeypatch):
        """Webhook fire against a public host is allowed through."""
        import utils.webhooks as wh

        mock_config = MagicMock()
        mock_config.WEBHOOK_ENDPOINTS = [{"url": "https://example.com/hook", "events": None}]
        monkeypatch.setattr(wh, "config", mock_config)
        monkeypatch.setattr("sentry_sdk.capture_message", lambda *a, **k: None)
        monkeypatch.setattr(wh, "utcnow_iso", lambda: "2026-01-01T00:00:00Z")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post = AsyncMock(return_value=mock_resp)

        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
            with patch("httpx.AsyncClient.post", mock_post):
                delivered = await wh.fire_event("test.event", {"x": 1})

        assert mock_post.call_count == 1
        assert delivered == 1

    @pytest.mark.asyncio
    async def test_webhooks_guard_or_log_called(self, monkeypatch):
        """guard_or_log is invoked for every webhook URL processed."""
        import utils.webhooks as wh

        mock_config = MagicMock()
        mock_config.WEBHOOK_ENDPOINTS = [{"url": "http://192.168.0.1/hook", "events": None}]
        monkeypatch.setattr(wh, "config", mock_config)

        guard_calls: list = []

        def mock_guard(url, *, source_name):
            guard_calls.append((url, source_name))
            return False  # block it

        monkeypatch.setattr(wh, "guard_or_log", mock_guard)

        delivered = await wh.fire_event("test.event", {"x": 1})

        assert len(guard_calls) == 1
        assert guard_calls[0] == ("http://192.168.0.1/hook", "webhooks")
        assert delivered == 0


class TestCustomSourceSSRFGuard:
    @pytest.mark.asyncio
    async def test_custom_source_query_blocks_private_url(self, monkeypatch):
        """CustomApiSource.query() with a private-host base_url returns [] without fetching."""
        from app.data_sources.custom import CustomApiSource

        monkeypatch.setattr("sentry_sdk.capture_message", lambda *a, **k: None)

        source = CustomApiSource(
            source_id="evil",
            display_name="Evil API",
            base_url="http://192.168.1.1",
        )

        get_calls: list = []
        mock_get = AsyncMock()

        async def track_get(self_inner, *args, **kwargs):
            get_calls.append(args)
            return await mock_get(*args, **kwargs)

        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("192.168.1.1", 0))]):
            with patch("httpx.AsyncClient.get", track_get):
                results = await source.query("test query")

        assert results == []
        assert len(get_calls) == 0, "httpx.get must never be called for a blocked URL"

    @pytest.mark.asyncio
    async def test_custom_source_test_connection_blocks_private_url(self, monkeypatch):
        """CustomApiSource.test_connection() with a private host returns (False, 'ssrf_blocked')."""
        from app.data_sources.custom import CustomApiSource

        monkeypatch.setattr("sentry_sdk.capture_message", lambda *a, **k: None)

        source = CustomApiSource(
            source_id="evil",
            display_name="Evil API",
            base_url="http://10.0.0.1",
        )

        get_calls: list = []

        async def track_get(self_inner, *args, **kwargs):
            get_calls.append(args)
            raise AssertionError("httpx.get must not be called")

        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.1", 0))]):
            with patch("httpx.AsyncClient.get", track_get):
                ok, reason = await source.test_connection()

        assert ok is False
        assert reason == "ssrf_blocked"
        assert len(get_calls) == 0

    @pytest.mark.asyncio
    async def test_custom_source_guard_or_log_called(self, monkeypatch):
        """guard_or_log is invoked with source_name='custom_source' in query()."""
        from app.data_sources import custom as custom_mod

        guard_calls: list = []

        def mock_guard(url, *, source_name):
            guard_calls.append((url, source_name))
            return False

        monkeypatch.setattr(custom_mod, "guard_or_log", mock_guard)

        source = custom_mod.CustomApiSource(
            source_id="t1",
            display_name="Test",
            base_url="http://192.168.0.1",
        )
        results = await source.query("anything")

        assert len(guard_calls) == 1
        assert guard_calls[0][1] == "custom_source"
        assert results == []
