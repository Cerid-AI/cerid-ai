# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""RA-57: EXTERNAL_VERIFY_MODEL and its two retry knobs were computed and
parsed in settings.py but never reached the external verification call
path — this covers both now being wired in.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

_SUPPORTED_JSON = '{"verdict": "supported", "confidence": 0.9, "reasoning": "matches"}'


def _llm_message(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


class TestExternalVerifyModelOverride:
    """An explicitly-set EXTERNAL_VERIFY_MODEL pins the model used."""

    @pytest.mark.asyncio
    async def test_overrides_cross_model_pool_pick(self):
        from core.agents.hallucination.verification import _verify_claim_externally

        captured: dict = {}

        async def _fake_call_llm_raw(messages, **kwargs):
            captured["model"] = kwargs.get("model")
            return _llm_message(_SUPPORTED_JSON)

        with (
            patch("config.ENABLE_EXTERNAL_VERIFICATION", True),
            patch("config.EXTERNAL_VERIFY_MODEL", "openrouter/pinned/model"),
            patch("core.utils.llm_client.call_llm_raw", _fake_call_llm_raw),
            patch.dict("os.environ", {"EXTERNAL_VERIFY_MODEL": "openrouter/pinned/model"}),
        ):
            verdict = await _verify_claim_externally("The sky is blue")

        assert verdict["verification_method"] == "cross_model"
        assert captured["model"] == "openrouter/pinned/model"

    @pytest.mark.asyncio
    async def test_overrides_web_search_model(self):
        from core.agents.hallucination.verification import _verify_claim_externally

        captured: dict = {}

        async def _fake_call_llm_raw(messages, **kwargs):
            captured["model"] = kwargs.get("model")
            return _llm_message(_SUPPORTED_JSON)

        with (
            patch("config.ENABLE_EXTERNAL_VERIFICATION", True),
            patch("config.EXTERNAL_VERIFY_MODEL", "openrouter/pinned/model"),
            patch("core.utils.llm_client.call_llm_raw", _fake_call_llm_raw),
            patch.dict("os.environ", {"EXTERNAL_VERIFY_MODEL": "openrouter/pinned/model"}),
        ):
            verdict = await _verify_claim_externally(
                "Some current event claim",
                force_web_search=True,
                deadline=time.monotonic() + 30.0,
            )

        assert verdict["verification_method"] == "web_search"
        assert captured["model"] == "openrouter/pinned/model"

    @pytest.mark.asyncio
    async def test_unset_leaves_routing_untouched(self):
        """No env var set → the pool/routing pick is used, not left blank."""
        from core.agents.hallucination.verification import _verify_claim_externally

        captured: dict = {}

        async def _fake_call_llm_raw(messages, **kwargs):
            captured["model"] = kwargs.get("model")
            return _llm_message(_SUPPORTED_JSON)

        with (
            patch("config.ENABLE_EXTERNAL_VERIFICATION", True),
            patch("core.utils.llm_client.call_llm_raw", _fake_call_llm_raw),
        ):
            import os
            os.environ.pop("EXTERNAL_VERIFY_MODEL", None)
            verdict = await _verify_claim_externally("The sky is blue")

        assert verdict["verification_method"] == "cross_model"
        assert captured["model"]  # some routed model, not empty/None


class TestExternalVerifyRetry:
    """EXTERNAL_VERIFY_RETRY_ATTEMPTS / BASE_DELAY govern transient retries."""

    @pytest.mark.asyncio
    async def test_retries_transient_timeout_then_succeeds(self):
        from core.agents.hallucination.verification import _verify_claim_externally

        call_count = 0

        async def _flaky_call_llm_raw(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.TimeoutException("timed out")
            return _llm_message(_SUPPORTED_JSON)

        with (
            patch("config.ENABLE_EXTERNAL_VERIFICATION", True),
            patch("config.EXTERNAL_VERIFY_RETRY_ATTEMPTS", 3),
            patch("config.EXTERNAL_VERIFY_RETRY_BASE_DELAY", 0.001),
            patch("core.utils.llm_client.call_llm_raw", _flaky_call_llm_raw),
            patch("asyncio.sleep", AsyncMock(return_value=None)),
        ):
            verdict = await _verify_claim_externally("The sky is blue")

        assert call_count == 2
        assert verdict["status"] == "verified"
        assert verdict["verification_method"] == "cross_model"

    @pytest.mark.asyncio
    async def test_gives_up_after_max_attempts(self):
        """Exhausting retries surfaces as the existing 'uncertain' failure verdict."""
        from core.agents.hallucination.verification import _verify_claim_externally

        call_count = 0

        async def _always_times_out(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.TimeoutException("timed out")

        with (
            patch("config.ENABLE_EXTERNAL_VERIFICATION", True),
            patch("config.EXTERNAL_VERIFY_RETRY_ATTEMPTS", 2),
            patch("config.EXTERNAL_VERIFY_RETRY_BASE_DELAY", 0.001),
            patch("core.utils.llm_client.call_llm_raw", _always_times_out),
            patch("asyncio.sleep", AsyncMock(return_value=None)),
        ):
            verdict = await _verify_claim_externally("The sky is blue")

        assert call_count == 2
        assert verdict["status"] == "uncertain"
        assert verdict["verification_method"].endswith("_failed")

    @pytest.mark.asyncio
    async def test_does_not_retry_non_retryable_4xx(self):
        """A 400 (bad request) is not a transient failure — no retry."""
        from core.agents.hallucination.verification import _verify_claim_externally

        call_count = 0

        async def _bad_request(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            raise _http_status_error(400)

        with (
            patch("config.ENABLE_EXTERNAL_VERIFICATION", True),
            patch("config.EXTERNAL_VERIFY_RETRY_ATTEMPTS", 3),
            patch("config.EXTERNAL_VERIFY_RETRY_BASE_DELAY", 0.001),
            patch("core.utils.llm_client.call_llm_raw", _bad_request),
            patch("asyncio.sleep", AsyncMock(return_value=None)),
        ):
            verdict = await _verify_claim_externally("The sky is blue")

        assert call_count == 1
        assert verdict["status"] == "uncertain"
        assert verdict["verification_method"].endswith("_failed")

    @pytest.mark.asyncio
    async def test_retries_5xx_then_succeeds(self):
        from core.agents.hallucination.verification import _verify_claim_externally

        call_count = 0

        async def _flaky_5xx(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise _http_status_error(503)
            return _llm_message(_SUPPORTED_JSON)

        with (
            patch("config.ENABLE_EXTERNAL_VERIFICATION", True),
            patch("config.EXTERNAL_VERIFY_RETRY_ATTEMPTS", 3),
            patch("config.EXTERNAL_VERIFY_RETRY_BASE_DELAY", 0.001),
            patch("core.utils.llm_client.call_llm_raw", _flaky_5xx),
            patch("asyncio.sleep", AsyncMock(return_value=None)),
        ):
            verdict = await _verify_claim_externally("The sky is blue")

        assert call_count == 2
        assert verdict["status"] == "verified"


_BATCH_SUPPORTED_JSON = (
    '[{"claim_index": 0, "verdict": "supported", "confidence": 0.9, '
    '"reasoning": "matches"}]'
)


class TestBatchExternalVerifyModelOverride:
    """RA-57: verify_claims_batch_external is a sibling live call path to
    _verify_claim_externally (streaming.py routes >=2 concurrent
    current-event claims through it) and must honor the same
    EXTERNAL_VERIFY_MODEL override.
    """

    @pytest.mark.asyncio
    async def test_overrides_routed_model(self):
        from core.agents.hallucination.verification import verify_claims_batch_external

        captured: dict = {}

        async def _fake_call_llm_raw(messages, **kwargs):
            captured["model"] = kwargs.get("model")
            return _llm_message(_BATCH_SUPPORTED_JSON)

        with (
            patch("config.EXTERNAL_VERIFY_MODEL", "openrouter/pinned/model"),
            patch("core.utils.llm_client.call_llm_raw", _fake_call_llm_raw),
            patch.dict("os.environ", {"EXTERNAL_VERIFY_MODEL": "openrouter/pinned/model"}),
        ):
            results = await verify_claims_batch_external(
                [(0, "The sky is blue")], model="routed/original/model"
            )

        assert captured["model"] == "openrouter/pinned/model"
        assert results[0]["verification_model"] == "openrouter/pinned/model"

    @pytest.mark.asyncio
    async def test_unset_leaves_routed_model_untouched(self):
        from core.agents.hallucination.verification import verify_claims_batch_external

        captured: dict = {}

        async def _fake_call_llm_raw(messages, **kwargs):
            captured["model"] = kwargs.get("model")
            return _llm_message(_BATCH_SUPPORTED_JSON)

        with patch("core.utils.llm_client.call_llm_raw", _fake_call_llm_raw):
            import os
            os.environ.pop("EXTERNAL_VERIFY_MODEL", None)
            results = await verify_claims_batch_external(
                [(0, "The sky is blue")], model="routed/original/model"
            )

        assert captured["model"] == "routed/original/model"
        assert results[0]["verification_model"] == "routed/original/model"


class TestBatchExternalVerifyRetry:
    """RA-57: the batch path must retry transient failures like the
    single-claim path does, instead of calling call_llm_raw directly.
    """

    @pytest.mark.asyncio
    async def test_retries_transient_timeout_then_succeeds(self):
        from core.agents.hallucination.verification import verify_claims_batch_external

        call_count = 0

        async def _flaky_call_llm_raw(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.TimeoutException("timed out")
            return _llm_message(_BATCH_SUPPORTED_JSON)

        with (
            patch("config.EXTERNAL_VERIFY_RETRY_ATTEMPTS", 3),
            patch("config.EXTERNAL_VERIFY_RETRY_BASE_DELAY", 0.001),
            patch("core.utils.llm_client.call_llm_raw", _flaky_call_llm_raw),
            patch("asyncio.sleep", AsyncMock(return_value=None)),
        ):
            results = await verify_claims_batch_external(
                [(0, "The sky is blue")], model="routed/original/model"
            )

        assert call_count == 2
        assert results[0]["status"] == "verified"

    @pytest.mark.asyncio
    async def test_retries_5xx_then_succeeds(self):
        from core.agents.hallucination.verification import verify_claims_batch_external

        call_count = 0

        async def _flaky_5xx(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise _http_status_error(503)
            return _llm_message(_BATCH_SUPPORTED_JSON)

        with (
            patch("config.EXTERNAL_VERIFY_RETRY_ATTEMPTS", 3),
            patch("config.EXTERNAL_VERIFY_RETRY_BASE_DELAY", 0.001),
            patch("core.utils.llm_client.call_llm_raw", _flaky_5xx),
            patch("asyncio.sleep", AsyncMock(return_value=None)),
        ):
            results = await verify_claims_batch_external(
                [(0, "The sky is blue")], model="routed/original/model"
            )

        assert call_count == 2
        assert results[0]["status"] == "verified"

    @pytest.mark.asyncio
    async def test_gives_up_after_max_attempts_returns_empty(self):
        """Exhausting retries falls into the existing except block — an
        empty result dict, same as any other batch-call failure (callers
        fall back to individual verification, per the docstring)."""
        from core.agents.hallucination.verification import verify_claims_batch_external

        call_count = 0

        async def _always_times_out(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.TimeoutException("timed out")

        with (
            patch("config.EXTERNAL_VERIFY_RETRY_ATTEMPTS", 2),
            patch("config.EXTERNAL_VERIFY_RETRY_BASE_DELAY", 0.001),
            patch("core.utils.llm_client.call_llm_raw", _always_times_out),
            patch("asyncio.sleep", AsyncMock(return_value=None)),
        ):
            results = await verify_claims_batch_external(
                [(0, "The sky is blue")], model="routed/original/model"
            )

        assert call_count == 2
        assert results == {}
