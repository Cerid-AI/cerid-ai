# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for Phase 3.3 (2026-07-13 quality program) independent search evidence.

Verification's web-search verdicts previously sourced URLs almost exclusively
from OpenRouter ``:online``-annotation citations (single-vendor sourcing).
These tests cover the SearXNG/Tavily supplement wired into
``_verify_claim_externally`` via ``_independent_search_evidence_urls``:

1. Skips gracefully when no real provider is configured (never calls search).
2. Skips gracefully when the per-claim deadline budget is exhausted.
3. Merges + dedups independent URLs with the annotation-sourced URLs.
4. Never fires for non-web (cross_model) claims.
5. Never raises into the caller when the search backend errors.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

_VERIFY_MOD = "core.agents.hallucination.verification"


def _llm_message(content: str, annotations: list[dict] | None = None) -> dict:
    """Shape a call_llm_raw() return value with one choice/message."""
    message: dict = {"content": content}
    if annotations is not None:
        message["annotations"] = annotations
    return {"choices": [{"message": message}]}


_SUPPORTED_JSON = '{"verdict": "supported", "confidence": 0.9, "reasoning": "matches"}'


class TestIndependentSearchEvidenceUrls:
    """Unit tests for the standalone helper."""

    @pytest.mark.asyncio
    async def test_skips_when_no_real_provider_configured(self):
        from core.agents.hallucination.verification import (
            _independent_search_evidence_urls,
        )

        search_mock = AsyncMock()
        with (
            patch("utils.web_search.has_real_search_provider", return_value=False),
            patch("utils.web_search.search_and_verify", search_mock),
        ):
            urls = await _independent_search_evidence_urls("claim text", deadline=None)

        assert urls == []
        search_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_budget_exhausted(self):
        from core.agents.hallucination.verification import (
            _independent_search_evidence_urls,
        )

        search_mock = AsyncMock()
        with (
            patch("utils.web_search.has_real_search_provider", return_value=True),
            patch("utils.web_search.search_and_verify", search_mock),
        ):
            # 0.5s remaining is below _MIN_EXTERNAL_CALL_BUDGET_S (3.0s).
            urls = await _independent_search_evidence_urls(
                "claim text", deadline=time.monotonic() + 0.5,
            )

        assert urls == []
        search_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_urls_when_configured_and_budget_ok(self):
        from core.agents.hallucination.verification import (
            _independent_search_evidence_urls,
        )

        search_mock = AsyncMock(return_value={
            "results": [
                {"url": "https://example.org/a", "title": "A"},
                {"url": "https://example.org/b", "title": "B"},
                {"url": "", "title": "no-url"},
            ],
        })
        with (
            patch("utils.web_search.has_real_search_provider", return_value=True),
            patch("utils.web_search.search_and_verify", search_mock),
        ):
            urls = await _independent_search_evidence_urls(
                "claim text", deadline=time.monotonic() + 30.0,
            )

        assert urls == ["https://example.org/a", "https://example.org/b"]
        search_mock.assert_awaited_once()
        _, kwargs = search_mock.await_args
        assert kwargs["max_results"] == 3  # _WEB_SEARCH_EVIDENCE_TOP_N

    @pytest.mark.asyncio
    async def test_never_raises_on_search_backend_error(self):
        from core.agents.hallucination.verification import (
            _independent_search_evidence_urls,
        )

        with (
            patch("utils.web_search.has_real_search_provider", return_value=True),
            patch(
                "utils.web_search.search_and_verify",
                AsyncMock(side_effect=RuntimeError("searxng unreachable")),
            ),
        ):
            urls = await _independent_search_evidence_urls("claim text", deadline=None)

        assert urls == []


class TestVerifyClaimExternallyMergesIndependentEvidence:
    """Integration: _verify_claim_externally merges + dedups + gates the merge."""

    @pytest.mark.asyncio
    async def test_web_search_path_merges_and_dedups_urls(self):
        from core.agents.hallucination.verification import _verify_claim_externally

        llm_mock = AsyncMock(return_value=_llm_message(
            _SUPPORTED_JSON,
            annotations=[
                {"type": "url_citation", "url_citation": {"url": "https://a.example/1"}},
            ],
        ))
        search_mock = AsyncMock(return_value={
            "results": [
                {"url": "https://a.example/1"},  # duplicate of annotation URL
                {"url": "https://b.example/2"},
            ],
        })

        with (
            patch("config.ENABLE_EXTERNAL_VERIFICATION", True),
            patch("core.utils.llm_client.call_llm_raw", llm_mock),
            patch("utils.web_search.has_real_search_provider", return_value=True),
            patch("utils.web_search.search_and_verify", search_mock),
        ):
            verdict = await _verify_claim_externally(
                "Some current event claim",
                force_web_search=True,
                deadline=time.monotonic() + 30.0,
            )

        assert verdict["verification_method"] == "web_search"
        assert verdict["source_urls"] == [
            "https://a.example/1",
            "https://b.example/2",
        ]
        search_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cross_model_path_never_calls_independent_search(self):
        """Non-web-search claims must not trigger an extra search call."""
        from core.agents.hallucination.verification import _verify_claim_externally

        llm_mock = AsyncMock(return_value=_llm_message(_SUPPORTED_JSON))
        search_mock = AsyncMock()

        with (
            patch("config.ENABLE_EXTERNAL_VERIFICATION", True),
            patch("core.utils.llm_client.call_llm_raw", llm_mock),
            patch("utils.web_search.has_real_search_provider", return_value=True),
            patch("utils.web_search.search_and_verify", search_mock),
        ):
            verdict = await _verify_claim_externally(
                "The sky is blue",
                deadline=time.monotonic() + 30.0,
            )

        assert verdict["verification_method"] == "cross_model"
        search_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_web_search_path_skips_independent_search_under_tight_budget(self):
        """The annotation URLs still return even when too little budget remains
        by the time the supplemental search call would fire.

        ``_remaining_budget`` is called 3 times on this path: the initial
        per-claim gate, the LLM-call timeout clamp, and the independent-search
        gate — deterministically drive the third call below
        ``_MIN_EXTERNAL_CALL_BUDGET_S`` without depending on real wall-clock
        elapsed time between mocked calls.
        """
        from core.agents.hallucination.verification import _verify_claim_externally

        llm_mock = AsyncMock(return_value=_llm_message(
            _SUPPORTED_JSON,
            annotations=[
                {"type": "url_citation", "url_citation": {"url": "https://a.example/1"}},
            ],
        ))
        search_mock = AsyncMock()

        with (
            patch("config.ENABLE_EXTERNAL_VERIFICATION", True),
            patch("core.utils.llm_client.call_llm_raw", llm_mock),
            patch("utils.web_search.has_real_search_provider", return_value=True),
            patch("utils.web_search.search_and_verify", search_mock),
            patch(f"{_VERIFY_MOD}._remaining_budget", side_effect=[10.0, 10.0, 1.0]),
        ):
            verdict = await _verify_claim_externally(
                "Some current event claim",
                force_web_search=True,
                deadline=time.monotonic() + 30.0,
            )

        assert verdict["source_urls"] == ["https://a.example/1"]
        search_mock.assert_not_called()
