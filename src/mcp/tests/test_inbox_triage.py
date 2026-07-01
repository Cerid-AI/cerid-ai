# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for inbox_triage agent — Phase J Day 1."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.data_sources.base import DataSource, DataSourceResult


class _StubSource(DataSource):
    def __init__(self, name: str, results: list[DataSourceResult]):
        self.name = name
        self.description = f"Stub {name}"
        self.enabled = True
        self._results = results
        self._configured = True

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        return self._results

    def is_configured(self) -> bool:
        return self._configured


# ── helper builders ────────────────────────────────────────────────────

def _msg(subject: str, body: str, sender: str = "alice@example.com") -> DataSourceResult:
    return DataSourceResult(
        title=subject,
        content=body,
        source_url=f"mailto:{sender}",
        source_name=sender,
        confidence=0.8,
    )


# ── thread grouping ────────────────────────────────────────────────────

class TestThreadGrouping:
    def test_drops_re_fwd_prefixes(self):
        from core.agents.inbox_triage import _extract_thread_id
        assert _extract_thread_id(_msg("Q3 plan", "x")) == "q3 plan"
        assert _extract_thread_id(_msg("Re: Q3 plan", "x")) == "q3 plan"
        assert _extract_thread_id(_msg("Fwd: Q3 plan", "x")) == "q3 plan"
        assert _extract_thread_id(_msg("RE:   Q3 plan", "x")) == "q3 plan"

    def test_empty_subject(self):
        from core.agents.inbox_triage import _extract_thread_id
        assert _extract_thread_id(_msg("", "x")) == "(no subject)"

    def test_groups_replies_with_original(self):
        from core.agents.inbox_triage import _group_by_thread
        msgs = [
            _msg("Q3 plan", "original"),
            _msg("Re: Q3 plan", "reply 1"),
            _msg("Re: Re: Q3 plan", "reply 2"),
            _msg("Different topic", "unrelated"),
        ]
        threads = _group_by_thread(msgs, source_name="gmail")
        assert "q3 plan" in threads
        assert len(threads["q3 plan"]) >= 2
        assert "different topic" in threads


# ── categorization parsing ─────────────────────────────────────────────

class TestSanitizeCategorization:
    def test_valid_dict_passes_through(self):
        from core.agents.inbox_triage import _sanitize_categorization
        result = _sanitize_categorization(
            {"category": "urgent", "summary": "fire", "suggested_action": "reply now"},
            fallback_messages=[_msg("x", "y")],
            thread_id="thread-1",
        )
        assert result["category"] == "urgent"
        assert result["summary"] == "fire"
        assert result["suggested_action"] == "reply now"

    def test_invalid_category_falls_back_to_heuristic(self):
        from core.agents.inbox_triage import _sanitize_categorization
        result = _sanitize_categorization(
            {"category": "made_up_category", "summary": "x"},
            fallback_messages=[_msg("test", "boring body")],
            thread_id="t",
        )
        # Heuristic picks something from CATEGORIES
        assert result["category"] in {
            "urgent", "actionable", "personal", "newsletter", "promo",
        }

    def test_truncates_long_summary(self):
        from core.agents.inbox_triage import _sanitize_categorization
        long = "x" * 1000
        result = _sanitize_categorization(
            {"category": "actionable", "summary": long, "suggested_action": "y"},
            fallback_messages=[_msg("t", "b")],
            thread_id="t",
        )
        assert len(result["summary"]) <= 500


class TestParseTriageResponse:
    def test_handles_code_fenced_json(self):
        from core.agents.inbox_triage import _parse_triage_response
        raw = """```json
{"category": "urgent", "summary": "yes", "suggested_action": "reply"}
```"""
        result = _parse_triage_response(
            raw,
            fallback_messages=[_msg("t", "b")],
            thread_id="t",
        )
        assert result["category"] == "urgent"

    def test_handles_dict_directly(self):
        from core.agents.inbox_triage import _parse_triage_response
        result = _parse_triage_response(
            {"category": "personal", "summary": "x", "suggested_action": "y"},
            fallback_messages=[_msg("t", "b")],
            thread_id="t",
        )
        assert result["category"] == "personal"

    def test_extracts_json_from_prose(self):
        from core.agents.inbox_triage import _parse_triage_response
        raw = (
            'Sure! Here is your categorization: '
            '{"category": "newsletter", "summary": "weekly", "suggested_action": "skim"} '
            'Let me know if you need more.'
        )
        result = _parse_triage_response(
            raw,
            fallback_messages=[_msg("t", "b")],
            thread_id="t",
        )
        assert result["category"] == "newsletter"

    def test_falls_back_when_unparseable(self):
        from core.agents.inbox_triage import _parse_triage_response
        result = _parse_triage_response(
            "no json here at all",
            fallback_messages=[_msg("Sale! 50% off", "buy now")],
            thread_id="sale",
        )
        # Heuristic picks promo
        assert result["category"] == "promo"


# ── heuristic categorize ──────────────────────────────────────────────

class TestHeuristic:
    def test_urgent_keyword_match(self):
        from core.agents.inbox_triage import _heuristic_categorize
        result = _heuristic_categorize(
            [_msg("Server down", "urgent server emergency")],
            "server down",
        )
        assert result["category"] == "urgent"

    def test_promo_keyword_match(self):
        from core.agents.inbox_triage import _heuristic_categorize
        result = _heuristic_categorize(
            [_msg("Sale", "unsubscribe link below")],
            "sale",
        )
        assert result["category"] == "promo"

    def test_newsletter_keyword_match(self):
        from core.agents.inbox_triage import _heuristic_categorize
        result = _heuristic_categorize(
            [_msg("Updates", "weekly digest of news")],
            "updates",
        )
        assert result["category"] == "newsletter"

    def test_default_is_actionable(self):
        from core.agents.inbox_triage import _heuristic_categorize
        result = _heuristic_categorize(
            [_msg("Project plan", "thoughts on next steps")],
            "project plan",
        )
        assert result["category"] == "actionable"


# ── end-to-end ─────────────────────────────────────────────────────────

class TestTriageInboxes:
    @pytest.fixture(autouse=True)
    def _wire_inbox_di(self):
        # core/ reads the DataSourceRegistry via DI now; wire the real singleton
        # (the same one app startup injects) so the registry.get patches below
        # take effect. Reset after each test to avoid cross-test leakage.
        import core.agents.inbox_triage as _m
        from app.agents_di import wire_inbox_triage_di

        wire_inbox_triage_di()
        yield
        _m._registry = None

    @pytest.mark.asyncio
    async def test_skips_when_feature_off(self):
        from core.agents.inbox_triage import triage_inboxes
        with patch("config.features.is_feature_enabled", return_value=False):
            result = await triage_inboxes(persist=False)
        assert result.threads == []
        assert any(s["reason"] == "feature_gated" for s in result.skipped)

    @pytest.mark.asyncio
    async def test_skips_when_source_not_configured(self):
        """When a source isn't registered, we record skip + continue."""
        from core.agents.inbox_triage import triage_inboxes
        # Empty registry → both sources skipped
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.data_sources.base.registry.get", return_value=None),
        ):
            result = await triage_inboxes(persist=False)
        assert result.threads == []
        assert any(s["reason"] == "not_registered" for s in result.skipped)

    @pytest.mark.asyncio
    async def test_full_pipeline_with_llm_mock(self):
        """Two messages from Gmail, two from Outlook → 4 threads
        (subject-grouped), each categorized via mocked LLM."""
        from core.agents.inbox_triage import triage_inboxes

        gmail_results = [
            _msg("Q3 plan", "thoughts", "alice@example.com"),
            _msg("Re: Q3 plan", "reply", "alice@example.com"),
            _msg("Weekly newsletter", "subscribe info", "news@example.com"),
        ]
        outlook_results = [
            _msg("URGENT: outage", "server emergency", "ops@example.com"),
        ]

        gmail_src = _StubSource("gmail", gmail_results)
        outlook_src = _StubSource("outlook", outlook_results)

        def _registry_get(name):
            return {"gmail": gmail_src, "outlook": outlook_src}.get(name)

        # Mock LLM: returns category based on first message content
        async def _mock_llm(messages, **kwargs):
            user_msg = messages[0]["content"].lower()
            if "urgent" in user_msg or "emergency" in user_msg:
                return '{"category":"urgent","summary":"outage","suggested_action":"page on-call"}'
            if "newsletter" in user_msg or "subscribe" in user_msg:
                return '{"category":"newsletter","summary":"weekly","suggested_action":"skim"}'
            return '{"category":"actionable","summary":"plan thread","suggested_action":"reply by EOD"}'

        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.data_sources.base.registry.get", side_effect=_registry_get),
            patch("core.utils.internal_llm.call_internal_llm",
                  new_callable=AsyncMock, side_effect=_mock_llm),
        ):
            result = await triage_inboxes(persist=False)

        # 3 threads from gmail (Q3 plan grouped + newsletter) + 1 from outlook
        assert len(result.threads) == 3
        # Source mix
        sources = {t.source for t in result.threads}
        assert sources == {"gmail", "outlook"}
        # by_category populated
        assert result.by_category["urgent"] >= 1
        assert "gmail" in result.sources_queried
        assert "outlook" in result.sources_queried

    @pytest.mark.asyncio
    async def test_persists_when_persist_true(self):
        from core.agents.inbox_triage import triage_inboxes

        gmail_src = _StubSource("gmail", [_msg("test", "body")])

        def _registry_get(name):
            return gmail_src if name == "gmail" else None

        async def _mock_llm(messages, **kwargs):
            return '{"category":"actionable","summary":"x","suggested_action":"y"}'

        # Mock the httpx POST inside _persist_to_kb
        class _Resp:
            status_code = 200
            def json(self):
                return {"artifact_id": "art:abc123"}

        class _Client:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                pass
            async def post(self, url, json, headers):
                return _Resp()

        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.data_sources.base.registry.get", side_effect=_registry_get),
            patch("core.utils.internal_llm.call_internal_llm",
                  new_callable=AsyncMock, side_effect=_mock_llm),
            patch("httpx.AsyncClient", return_value=_Client()),
        ):
            result = await triage_inboxes(persist=True, mcp_base_url="http://test")

        assert len(result.threads) == 1
        assert result.threads[0].artifact_id == "art:abc123"

    @pytest.mark.asyncio
    async def test_categorize_thread_handles_llm_failure(self):
        """When the LLM call raises, the agent falls back to heuristic
        rather than crashing the whole batch."""
        from core.agents.inbox_triage import _categorize_thread
        with patch(
            "core.utils.internal_llm.call_internal_llm",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM down"),
        ):
            result = await _categorize_thread(
                "test thread",
                [_msg("Server outage urgent", "down right now")],
            )
        # Heuristic kicks in
        assert result["category"] in {"urgent", "actionable"}
