# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""WB-58: the verifier's "current date" must honour the operator's TZ env
var rather than always using the container's naive UTC date, which
misjudges recency for part of every evening in timezones behind UTC.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

# 2026-01-01T02:00:00 UTC is still 2025-12-31 in America/Los_Angeles (UTC-8).
_FIXED_UTC = datetime(2026, 1, 1, 2, 0, 0, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    """Stand-in for ``datetime`` with a frozen ``now()``."""

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001 — matches datetime.now's signature
        if tz is None:
            return _FIXED_UTC.replace(tzinfo=None)
        return _FIXED_UTC.astimezone(tz)


def _llm_message(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


_SUPPORTED_JSON = '{"verdict": "supported", "confidence": 0.9, "reasoning": "matches"}'


class TestResolveVerificationDate:
    """Unit tests for the standalone date-resolution helper."""

    def test_uses_tz_env_var_when_set(self):
        from core.agents.hallucination.verification import _resolve_verification_date

        with (
            patch.dict(os.environ, {"TZ": "America/Los_Angeles"}),
            patch("core.agents.hallucination.verification.datetime", _FixedDatetime),
        ):
            assert _resolve_verification_date() == "2025-12-31"

    def test_falls_back_to_naive_utc_without_tz(self):
        from core.agents.hallucination.verification import _resolve_verification_date

        with patch("core.agents.hallucination.verification.datetime", _FixedDatetime):
            os.environ.pop("TZ", None)
            assert _resolve_verification_date() == "2026-01-01"

    def test_falls_back_to_naive_utc_on_invalid_tz(self):
        from core.agents.hallucination.verification import _resolve_verification_date

        with (
            patch.dict(os.environ, {"TZ": "Not/A_Real_Zone"}),
            patch("core.agents.hallucination.verification.datetime", _FixedDatetime),
        ):
            assert _resolve_verification_date() == "2026-01-01"


class TestVerifyClaimExternallySurfacesAssumedDate:
    """Integration: the resolved date reaches both the LLM prompt and the verdict."""

    @pytest.mark.asyncio
    async def test_web_search_path_carries_resolved_date(self):
        from core.agents.hallucination.verification import _verify_claim_externally

        captured: dict = {}

        async def _fake_call_llm_raw(messages, **kwargs):
            captured["messages"] = messages
            return _llm_message(_SUPPORTED_JSON)

        with (
            patch("config.ENABLE_EXTERNAL_VERIFICATION", True),
            patch("core.utils.llm_client.call_llm_raw", _fake_call_llm_raw),
            patch.dict(os.environ, {"TZ": "America/Los_Angeles"}),
            patch("core.agents.hallucination.verification.datetime", _FixedDatetime),
        ):
            verdict = await _verify_claim_externally(
                "Some current event claim",
                force_web_search=True,
                deadline=time.monotonic() + 30.0,
            )

        assert verdict["assumed_date"] == "2025-12-31"
        system_content = captured["messages"][0]["content"]
        assert "2025-12-31" in system_content

    @pytest.mark.asyncio
    async def test_cross_model_path_has_no_assumed_date(self):
        """Static (non-web-search) claims never inject a current-date prompt."""
        from core.agents.hallucination.verification import _verify_claim_externally

        llm_mock = AsyncMock(return_value=_llm_message(_SUPPORTED_JSON))

        with (
            patch("config.ENABLE_EXTERNAL_VERIFICATION", True),
            patch("core.utils.llm_client.call_llm_raw", llm_mock),
        ):
            verdict = await _verify_claim_externally(
                "The sky is blue",
                deadline=time.monotonic() + 30.0,
            )

        assert "assumed_date" not in verdict
