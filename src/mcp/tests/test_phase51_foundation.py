# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for Phase 51 foundation modules.

Covers: errors.py, utils/error_handler.py, utils/degradation.py, config/constants.py.
These modules form the error handling, degradation, and configuration foundation.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from config.constants import (
    BIFROST_TIMEOUT,
    CHUNK_OVERLAP_RATIO,
    CONFIDENCE_CEILING,
    CONFIDENCE_FLOOR,
    DEFAULT_TOP_K,
    HEALTH_CACHE_TTL,
    MAX_ARTIFACT_LIST,
    MAX_CLAIMS_PER_RESPONSE,
    MAX_UPLOAD_SIZE_BYTES,
    MONTHLY_BUDGET_USD,
    OLLAMA_CONNECT_TIMEOUT,
    OLLAMA_READ_TIMEOUT,
    PARENT_CHILD_RATIO_MAX,
    PARENT_CHILD_RATIO_MIN,
    QUERY_CACHE_TTL,
    RATE_LIMIT_WINDOW_SECONDS,
    RETRIEVAL_CACHE_TTL,
    VERIFICATION_TIMEOUT,
)
from errors import (
    CeridError,
    CreditExhaustedError,
    IngestionError,
    ProviderError,
    RateLimitError,
    RetrievalError,
    RoutingError,
    VerificationError,
    error_response,
)
from utils.degradation import DegradationManager, DegradationTier
from utils.error_handler import handle_errors

# ---------------------------------------------------------------------------
# TestCeridErrorHierarchy
# ---------------------------------------------------------------------------


class TestCeridErrorHierarchy:
    """Verify the CeridError exception tree, error codes, and helper."""

    def test_cerid_error_has_error_code(self) -> None:
        exc = CeridError("msg", error_code="TEST_CODE")
        assert exc.error_code == "TEST_CODE"

    def test_default_error_code_uses_prefix(self) -> None:
        exc = CeridError("msg")
        assert exc.error_code == "CERID_ERROR"

    def test_ingestion_error_prefix(self) -> None:
        exc = IngestionError("msg")
        assert exc.error_code.startswith("INGESTION_")

    def test_retrieval_error_prefix(self) -> None:
        exc = RetrievalError("msg")
        assert exc.error_code.startswith("RETRIEVAL_")

    def test_provider_error_prefix(self) -> None:
        exc = ProviderError("msg")
        assert exc.error_code.startswith("PROVIDER_")

    def test_credit_exhausted_inherits_provider(self) -> None:
        exc = CreditExhaustedError("quota hit")
        assert isinstance(exc, ProviderError)
        assert isinstance(exc, CeridError)

    def test_rate_limit_inherits_provider(self) -> None:
        exc = RateLimitError("429")
        assert isinstance(exc, ProviderError)
        assert isinstance(exc, CeridError)

    def test_error_response_helper(self) -> None:
        exc = CeridError("boom", error_code="TEST_BOOM")
        resp = error_response(exc)
        assert resp["error_code"] == "TEST_BOOM"
        assert resp["message"] == "boom"
        assert "details" in resp

    def test_error_details_preserved(self) -> None:
        details = {"key": "value", "count": 42}
        exc = CeridError("with details", error_code="DET", details=details)
        resp = error_response(exc)
        assert resp["details"] == details


# ---------------------------------------------------------------------------
# TestHandleErrors
# ---------------------------------------------------------------------------


class TestHandleErrors:
    """Verify the @handle_errors() decorator for sync and async paths."""

    def test_cerid_error_reraises(self) -> None:
        @handle_errors()
        def fn():
            raise IngestionError("parse fail")

        with pytest.raises(IngestionError, match="parse fail"):
            fn()

    def test_generic_exception_wraps_routing_error(self) -> None:
        @handle_errors()
        def fn():
            raise ValueError("unexpected")

        with pytest.raises(RoutingError) as exc_info:
            fn()
        assert exc_info.value.error_code == "UNHANDLED_ERROR"

    def test_fallback_returns_value(self) -> None:
        @handle_errors(fallback=[])
        def fn():
            raise RuntimeError("kaboom")

        assert fn() == []

    def test_async_function_support(self) -> None:
        @handle_errors()
        async def fn():
            raise ValueError("async boom")

        with pytest.raises(RoutingError) as exc_info:
            asyncio.get_event_loop().run_until_complete(fn())
        assert exc_info.value.error_code == "UNHANDLED_ERROR"

    def test_async_cerid_error_reraises(self) -> None:
        @handle_errors()
        async def fn():
            raise VerificationError("bad claim")

        with pytest.raises(VerificationError, match="bad claim"):
            asyncio.get_event_loop().run_until_complete(fn())

    def test_async_fallback(self) -> None:
        @handle_errors(fallback="default")
        async def fn():
            raise RuntimeError("async fail")

        result = asyncio.get_event_loop().run_until_complete(fn())
        assert result == "default"


# ---------------------------------------------------------------------------
# TestDegradationTier
# ---------------------------------------------------------------------------


class TestDegradationTier:
    """Verify the DegradationTier enum and DegradationManager basics."""

    def test_tier_values(self) -> None:
        assert DegradationTier.FULL.value == "full"
        assert DegradationTier.LITE.value == "lite"
        assert DegradationTier.DIRECT.value == "direct"
        assert DegradationTier.CACHED.value == "cached"
        assert DegradationTier.OFFLINE.value == "offline"

    def test_default_tier_is_full(self) -> None:
        """When no breakers are open and Redis is up, tier should be FULL.

        We patch the internal helpers so the test doesn't need live services.
        """
        mgr = DegradationManager()
        with (
            patch("utils.degradation._is_breaker_open", return_value=False),
            patch("utils.degradation._redis_down", return_value=False),
        ):
            assert mgr.current_tier() == DegradationTier.FULL


# ---------------------------------------------------------------------------
# TestConstants
# ---------------------------------------------------------------------------


class TestConstants:
    """Sanity-check that numeric constants have sensible values."""

    @pytest.mark.parametrize(
        "value",
        [
            HEALTH_CACHE_TTL,
            OLLAMA_READ_TIMEOUT,
            OLLAMA_CONNECT_TIMEOUT,
            BIFROST_TIMEOUT,
            VERIFICATION_TIMEOUT,
            QUERY_CACHE_TTL,
            MONTHLY_BUDGET_USD,
            RATE_LIMIT_WINDOW_SECONDS,
            DEFAULT_TOP_K,
            RETRIEVAL_CACHE_TTL,
            MAX_CLAIMS_PER_RESPONSE,
            MAX_ARTIFACT_LIST,
            MAX_UPLOAD_SIZE_BYTES,
        ],
    )
    def test_constants_are_positive(self, value: float) -> None:
        assert value > 0

    def test_confidence_range(self) -> None:
        assert 0 <= CONFIDENCE_FLOOR < CONFIDENCE_CEILING <= 1

    def test_chunk_overlap_ratio(self) -> None:
        assert 0 < CHUNK_OVERLAP_RATIO < 1

    def test_parent_child_ratio(self) -> None:
        assert PARENT_CHILD_RATIO_MIN < PARENT_CHILD_RATIO_MAX
