# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the external-API base class and shared HTTP client (Phase API.1).

Verifies:
* ExternalAPIError attributes are set correctly.
* ExternalAPIAdapter._wrap_http_error converts HTTPStatusError and
  transport errors to ExternalAPIError and calls log_swallowed_error.
* get_http_client() returns a singleton and respects the User-Agent header.
* close_http_client() resets the singleton so the next call creates fresh.
* health_check() contract: must return bool, never raise.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Real httpx is available in the test env; the conftest stubs are
# _ensure_stub (only if not importable), so httpx itself is real here.
# ---------------------------------------------------------------------------
import httpx
import pytest

from app.services.external_apis.base import (
    ExternalAPIAdapter,
    ExternalAPIError,
    close_http_client,
    get_http_client,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Concrete stub for testing the abstract base
# ---------------------------------------------------------------------------


class _StubAdapter(ExternalAPIAdapter):
    slug = "stub"
    display_name = "Stub"
    requires_key = False

    async def lookup(self, *args: Any, **kwargs: Any) -> Any:
        return {}

    async def health_check(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# ExternalAPIError
# ---------------------------------------------------------------------------


class TestExternalAPIError:
    def test_attributes_set(self):
        err = ExternalAPIError(provider="wikipedia", detail="not found", status_code=404)
        assert err.provider == "wikipedia"
        assert err.detail == "not found"
        assert err.status_code == 404

    def test_str_contains_provider_and_code(self):
        err = ExternalAPIError(provider="arxiv", detail="timeout", status_code=0)
        s = str(err)
        assert "arxiv" in s
        assert "0" in s

    def test_default_status_code_is_zero(self):
        err = ExternalAPIError(provider="github", detail="transport error")
        assert err.status_code == 0


# ---------------------------------------------------------------------------
# ExternalAPIAdapter._wrap_http_error
# ---------------------------------------------------------------------------


class TestWrapHttpError:
    def setup_method(self):
        self.adapter = _StubAdapter()

    def test_wraps_http_status_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.request = MagicMock()
        exc = httpx.HTTPStatusError("service unavailable", request=mock_response.request, response=mock_response)

        with patch("app.services.external_apis.base.log_swallowed_error") as mock_log:
            result = self.adapter._wrap_http_error(exc)

        assert isinstance(result, ExternalAPIError)
        assert result.provider == "stub"
        assert result.status_code == 503
        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert "external_apis.stub" in call_args[0][0]

    def test_wraps_generic_httpx_error(self):
        exc = httpx.ConnectError("connection refused")

        with patch("app.services.external_apis.base.log_swallowed_error") as mock_log:
            result = self.adapter._wrap_http_error(exc)

        assert isinstance(result, ExternalAPIError)
        assert result.provider == "stub"
        assert result.status_code == 0
        mock_log.assert_called_once()

    def test_returns_external_api_error_on_any_exception(self):
        exc = Exception("unexpected")

        with patch("app.services.external_apis.base.log_swallowed_error"):
            result = self.adapter._wrap_http_error(exc)

        assert isinstance(result, ExternalAPIError)


# ---------------------------------------------------------------------------
# get_http_client / close_http_client
# ---------------------------------------------------------------------------


class TestHttpClient:
    async def test_get_http_client_returns_client(self):
        await close_http_client()  # reset state
        client = await get_http_client()
        assert isinstance(client, httpx.AsyncClient)

    async def test_get_http_client_is_singleton(self):
        await close_http_client()
        c1 = await get_http_client()
        c2 = await get_http_client()
        assert c1 is c2

    async def test_close_http_client_resets_singleton(self):
        await close_http_client()
        c1 = await get_http_client()
        await close_http_client()
        c2 = await get_http_client()
        # After close, a new client is created
        assert c1 is not c2

    async def test_client_has_user_agent(self):
        await close_http_client()
        client = await get_http_client()
        assert "cerid-ai" in client.headers.get("user-agent", "")

    async def test_client_has_timeout(self):
        await close_http_client()
        client = await get_http_client()
        # httpx.Timeout stores it as total timeout
        assert client.timeout.read is not None or client.timeout.connect is not None


# ---------------------------------------------------------------------------
# Abstract interface: health_check must return bool, not raise
# ---------------------------------------------------------------------------


class TestHealthCheckContract:
    async def test_stub_health_check_returns_bool(self):
        adapter = _StubAdapter()
        result = await adapter.health_check()
        assert isinstance(result, bool)

    async def test_stub_lookup_returns_dict(self):
        adapter = _StubAdapter()
        result = await adapter.lookup()
        assert isinstance(result, dict)
