# Copyright 2026 Cerid AI. Apache-2.0 license.
"""Tests for CeridError hierarchy, error_response helper, and error handler decorator."""

import pytest

from errors import (
    CeridError,
    ConfigError,
    CreditExhaustedError,
    FeatureGateError,
    IngestionError,
    ProviderError,
    RateLimitError,
    RetrievalError,
    RoutingError,
    SyncError,
    VerificationError,
    error_response,
)
from utils.error_handler import handle_errors


# ---------------------------------------------------------------------------
# Tests: CeridError base class
# ---------------------------------------------------------------------------

class TestCeridError:
    def test_basic_message(self):
        err = CeridError("something failed")
        assert str(err) == "something failed"
        assert err.error_code == "CERID_ERROR"
        assert err.details is None

    def test_custom_error_code(self):
        err = CeridError("oops", error_code="CUSTOM_CODE")
        assert err.error_code == "CUSTOM_CODE"

    def test_details_dict(self):
        details = {"key": "value"}
        err = CeridError("oops", details=details)
        assert err.details == details

    def test_inherits_from_exception(self):
        assert issubclass(CeridError, Exception)


# ---------------------------------------------------------------------------
# Tests: Error subclasses
# ---------------------------------------------------------------------------

class TestErrorSubclasses:
    def test_ingestion_error_prefix(self):
        err = IngestionError("parse failed")
        assert err.error_code.startswith("INGESTION_")

    def test_retrieval_error_prefix(self):
        err = RetrievalError("chroma down")
        assert err.error_code.startswith("RETRIEVAL_")

    def test_verification_error_prefix(self):
        err = VerificationError("claim parse failed")
        assert err.error_code.startswith("VERIFICATION_")

    def test_routing_error_prefix(self):
        err = RoutingError("no model available")
        assert err.error_code.startswith("ROUTING_")

    def test_sync_error_prefix(self):
        err = SyncError("manifest conflict")
        assert err.error_code.startswith("SYNC_")

    def test_provider_error_prefix(self):
        err = ProviderError("api timeout")
        assert err.error_code.startswith("PROVIDER_")

    def test_credit_exhausted_inherits_provider(self):
        err = CreditExhaustedError("no credits")
        assert isinstance(err, ProviderError)
        assert isinstance(err, CeridError)

    def test_rate_limit_inherits_provider(self):
        err = RateLimitError("429")
        assert isinstance(err, ProviderError)

    def test_config_error_prefix(self):
        err = ConfigError("missing env var")
        assert err.error_code.startswith("CONFIG_")

    def test_feature_gate_error_prefix(self):
        err = FeatureGateError("requires pro tier")
        assert err.error_code.startswith("FEATURE_GATE_")


# ---------------------------------------------------------------------------
# Tests: error_response helper
# ---------------------------------------------------------------------------

class TestErrorResponse:
    def test_basic_conversion(self):
        err = IngestionError("parse failed", error_code="INGESTION_PDF_CORRUPT")
        resp = error_response(err)
        assert isinstance(resp, dict)
        assert resp["error_code"] == "INGESTION_PDF_CORRUPT"
        assert "parse failed" in resp["message"]

    def test_includes_details(self):
        err = CeridError("oops", details={"file": "test.pdf"})
        resp = error_response(err)
        assert resp["details"]["file"] == "test.pdf"


# ---------------------------------------------------------------------------
# Tests: @handle_errors decorator
# ---------------------------------------------------------------------------

class TestHandleErrors:
    def test_sync_function_passthrough(self):
        @handle_errors()
        def good_fn():
            return 42

        assert good_fn() == 42

    def test_sync_cerid_error_reraises(self):
        @handle_errors()
        def bad_fn():
            raise IngestionError("fail")

        with pytest.raises(IngestionError):
            bad_fn()

    def test_sync_generic_error_becomes_routing_error(self):
        @handle_errors()
        def bad_fn():
            raise ValueError("unexpected")

        with pytest.raises(RoutingError):
            bad_fn()

    def test_sync_fallback_returns_value(self):
        @handle_errors(fallback={"status": "degraded"})
        def bad_fn():
            raise ValueError("unexpected")

        result = bad_fn()
        assert result == {"status": "degraded"}

    @pytest.mark.asyncio
    async def test_async_function_passthrough(self):
        @handle_errors()
        async def good_fn():
            return 42

        assert await good_fn() == 42

    @pytest.mark.asyncio
    async def test_async_cerid_error_reraises(self):
        @handle_errors()
        async def bad_fn():
            raise RetrievalError("chroma timeout")

        with pytest.raises(RetrievalError):
            await bad_fn()
