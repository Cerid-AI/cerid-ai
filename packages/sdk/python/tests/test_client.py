# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Cerid Python SDK client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from cerid import AsyncCeridClient, CeridClient, CeridSDKError
from cerid.errors import (
    AuthenticationError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
    ValidationError,
    _raise_for_status,
)
from cerid.models import HealthResponse, QueryResponse
from cerid.resources.kb import KBResource
from cerid.resources.memory import MemoryResource
from cerid.resources.system import SystemResource
from cerid.resources.verify import VerifyResource


# ---------------------------------------------------------------------------
# Client construction
# ---------------------------------------------------------------------------


class TestCeridClientConstruction:
    def test_base_url_trailing_slash_stripped(self) -> None:
        client = CeridClient(base_url="http://localhost:8888/", client_id="test")
        assert client.base_url == "http://localhost:8888"
        client.close()

    def test_headers_include_client_id(self) -> None:
        client = CeridClient(base_url="http://localhost:8888", client_id="my-app")
        headers = client._build_headers()
        assert headers["X-Client-ID"] == "my-app"
        assert "X-API-Key" not in headers
        client.close()

    def test_headers_include_api_key_when_set(self) -> None:
        client = CeridClient(
            base_url="http://localhost:8888",
            client_id="my-app",
            api_key="secret-key",
        )
        headers = client._build_headers()
        assert headers["X-API-Key"] == "secret-key"
        client.close()

    def test_url_builder(self) -> None:
        client = CeridClient(base_url="http://localhost:8888", client_id="test")
        assert client._url("/health") == "http://localhost:8888/sdk/v1/health"
        assert client._url("/query") == "http://localhost:8888/sdk/v1/query"
        client.close()


# ---------------------------------------------------------------------------
# Resource access
# ---------------------------------------------------------------------------


class TestResourceAccess:
    def test_resource_properties_return_correct_types(self) -> None:
        client = CeridClient(base_url="http://localhost:8888", client_id="test")
        assert isinstance(client.kb, KBResource)
        assert isinstance(client.verify, VerifyResource)
        assert isinstance(client.memory, MemoryResource)
        assert isinstance(client.system, SystemResource)
        client.close()

    def test_resource_properties_are_cached(self) -> None:
        client = CeridClient(base_url="http://localhost:8888", client_id="test")
        assert client.kb is client.kb
        assert client.verify is client.verify
        assert client.memory is client.memory
        assert client.system is client.system
        client.close()

    def test_async_client_resource_types(self) -> None:
        client = AsyncCeridClient(base_url="http://localhost:8888", client_id="test")
        # Just verify attributes exist and are the right async types
        from cerid.resources.kb import AsyncKBResource
        from cerid.resources.memory import AsyncMemoryResource
        from cerid.resources.system import AsyncSystemResource
        from cerid.resources.verify import AsyncVerifyResource

        assert isinstance(client.kb, AsyncKBResource)
        assert isinstance(client.verify, AsyncVerifyResource)
        assert isinstance(client.memory, AsyncMemoryResource)
        assert isinstance(client.system, AsyncSystemResource)


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_sync_context_manager(self) -> None:
        with CeridClient(base_url="http://localhost:8888", client_id="test") as client:
            assert isinstance(client, CeridClient)

    @pytest.mark.asyncio
    async def test_async_context_manager(self) -> None:
        async with AsyncCeridClient(base_url="http://localhost:8888", client_id="test") as client:
            assert isinstance(client, AsyncCeridClient)


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def _mock_response(status_code: int, json_body: dict | None = None, headers: dict | None = None) -> httpx.Response:
    """Create a mock httpx.Response."""
    resp = httpx.Response(
        status_code=status_code,
        json=json_body or {},
        headers=headers or {},
        request=httpx.Request("GET", "http://test"),
    )
    return resp


class TestErrorMapping:
    def test_success_does_not_raise(self) -> None:
        resp = _mock_response(200, {"status": "ok"})
        _raise_for_status(resp)  # Should not raise

    def test_401_raises_authentication_error(self) -> None:
        resp = _mock_response(401, {"detail": "Invalid API key"})
        with pytest.raises(AuthenticationError) as exc_info:
            _raise_for_status(resp)
        assert exc_info.value.status_code == 401
        assert "Invalid API key" in str(exc_info.value)

    def test_403_raises_authentication_error(self) -> None:
        resp = _mock_response(403, {"detail": "Forbidden"})
        with pytest.raises(AuthenticationError) as exc_info:
            _raise_for_status(resp)
        assert exc_info.value.status_code == 403

    def test_404_raises_not_found_error(self) -> None:
        resp = _mock_response(404, {"detail": "Not found"})
        with pytest.raises(NotFoundError) as exc_info:
            _raise_for_status(resp)
        assert exc_info.value.status_code == 404

    def test_422_raises_validation_error(self) -> None:
        resp = _mock_response(422, {"detail": "field required"})
        with pytest.raises(ValidationError) as exc_info:
            _raise_for_status(resp)
        assert exc_info.value.status_code == 422

    def test_429_raises_rate_limit_error(self) -> None:
        resp = _mock_response(429, {"detail": "Rate limited"}, headers={"retry-after": "30"})
        with pytest.raises(RateLimitError) as exc_info:
            _raise_for_status(resp)
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 30.0

    def test_429_without_retry_after(self) -> None:
        resp = _mock_response(429, {"detail": "Rate limited"})
        with pytest.raises(RateLimitError) as exc_info:
            _raise_for_status(resp)
        assert exc_info.value.retry_after is None

    def test_503_raises_service_unavailable(self) -> None:
        resp = _mock_response(503, {"detail": "Backend down"})
        with pytest.raises(ServiceUnavailableError) as exc_info:
            _raise_for_status(resp)
        assert exc_info.value.status_code == 503

    def test_500_raises_base_sdk_error(self) -> None:
        resp = _mock_response(500, {"detail": "Internal error"})
        with pytest.raises(CeridSDKError) as exc_info:
            _raise_for_status(resp)
        assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# Resource method calls (with mocked HTTP)
# ---------------------------------------------------------------------------


class TestKBResource:
    def test_query_sends_correct_request(self) -> None:
        with CeridClient(base_url="http://localhost:8888", client_id="test") as client:
            mock_resp = _mock_response(200, {
                "context": "some context",
                "sources": [],
                "confidence": 0.85,
                "domains_searched": ["general"],
                "total_results": 1,
                "token_budget_used": 100,
                "graph_results": 0,
                "results": [],
            })
            client._http.post = MagicMock(return_value=mock_resp)

            result = client.kb.query("test query", top_k=3)

            assert isinstance(result, QueryResponse)
            assert result.confidence == 0.85
            assert result.context == "some context"
            client._http.post.assert_called_once()
            call_args = client._http.post.call_args
            assert "/sdk/v1/query" in call_args[0][0]

    def test_collections_sends_get(self) -> None:
        with CeridClient(base_url="http://localhost:8888", client_id="test") as client:
            mock_resp = _mock_response(200, {"collections": ["general", "code"], "total": 2})
            client._http.get = MagicMock(return_value=mock_resp)

            result = client.kb.collections()
            assert result.total == 2
            assert "general" in result.collections


class TestSystemResource:
    def test_health_parses_response(self) -> None:
        with CeridClient(base_url="http://localhost:8888", client_id="test") as client:
            mock_resp = _mock_response(200, {
                "status": "healthy",
                "version": "1.1.0",
                "services": {"chromadb": "connected"},
                "features": {"enable_self_rag": True},
            })
            client._http.get = MagicMock(return_value=mock_resp)

            result = client.system.health()
            assert isinstance(result, HealthResponse)
            assert result.status == "healthy"
            assert result.version == "1.1.0"

    def test_health_error_propagates(self) -> None:
        with CeridClient(base_url="http://localhost:8888", client_id="test") as client:
            mock_resp = _mock_response(503, {"detail": "Backend down"})
            client._http.get = MagicMock(return_value=mock_resp)

            with pytest.raises(ServiceUnavailableError):
                client.system.health()


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


class TestModels:
    def test_query_response_extra_fields_allowed(self) -> None:
        """Server can add new fields without breaking the client."""
        resp = QueryResponse.model_validate({
            "context": "hello",
            "confidence": 0.5,
            "new_field_from_server": True,
        })
        assert resp.context == "hello"
        # Extra field passes through
        assert resp.new_field_from_server is True  # type: ignore[attr-defined]

    def test_health_response_defaults(self) -> None:
        resp = HealthResponse.model_validate({})
        assert resp.status == ""
        assert resp.version == ""
        assert resp.services == {}
        assert resp.features == {}
