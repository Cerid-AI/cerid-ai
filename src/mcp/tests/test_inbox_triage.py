# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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

def _msg(
    subject: str,
    body: str,
    sender: str = "alice@example.com",
    metadata: dict | None = None,
) -> DataSourceResult:
    return DataSourceResult(
        title=subject,
        content=body,
        source_url=f"mailto:{sender}",
        source_name=sender,
        confidence=0.8,
        metadata=metadata,
    )


def _provider(thread_id: str = "thr-1", message_id: str = "msg-1") -> dict[str, str]:
    return {"provider_thread_id": thread_id, "provider_message_id": message_id}


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

    def test_provider_thread_id_beats_the_subject(self):
        from core.agents.inbox_triage import _extract_thread_id, _thread_writable
        msg = _msg("Re: Q3 plan", "x", metadata=_provider("thr-9", "msg-9"))
        assert _extract_thread_id(msg) == "thr-9"
        assert _thread_writable([msg]) is True
        assert _thread_writable([_msg("Q3 plan", "x")]) is False


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
            "urgent", "actionable", "personal", "newsletter", "promo", "spam",
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

    def test_send_action_cannot_leave_the_enum(self):
        from core.agents.inbox_triage import _sanitize_categorization
        result = _sanitize_categorization(
            {"category": "promo", "summary": "ad", "action": "send", "suggested_action": "archive"},
            fallback_messages=[_msg("t", "b")],
            thread_id="t",
        )
        assert result["action"] == "archive"
        assert result["suggested_action"] == "archive"


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

    def test_category_repair_asks_for_the_enum_only(self):
        from core.agents.inbox_actions import CATEGORIES
        from core.agents.inbox_triage import _category_repair_prompt

        text = _category_repair_prompt("body text")
        for name in CATEGORIES:
            assert name in text
        assert "body text" in text
        assert "payee" not in text
        assert "amount" not in text
        assert "No other keys" in text

    def test_a_card_object_is_not_a_category(self):
        from core.agents.inbox_triage import _model_reply

        assert _model_reply('{"payee":"northwind","amount":"40","currency":"USD"}') is None
        assert _model_reply('{"confidence":1.0}') is None

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
            [_msg("Server urgent", "down right now")],
            "server urgent",
        )
        assert result["category"] == "urgent"
        quiet = _heuristic_categorize(
            [_msg("Server down", "urgent server emergency")],
            "server down",
        )
        assert quiet["category"] != "urgent"

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
        _m._rag_route = None

    @pytest.mark.asyncio
    async def test_skips_when_feature_off(self):
        from core.agents.inbox_triage import triage_inboxes
        with patch("config.features.is_feature_enabled", return_value=False):
            result = await triage_inboxes(persist=False)
        assert result.threads == []
        assert any(s["reason"] == "feature_gated" for s in result.skipped)

    @pytest.mark.asyncio
    async def test_skips_when_source_not_configured(self):
        """When a source isn't registered, we record the skip and continue."""
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
            user_msg = messages[0]["content"].split("Thread:\n", 1)[-1].lower()
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

        gmail_src = _StubSource("gmail", [_msg("test", "body", metadata=_provider())])

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

    @pytest.mark.asyncio
    async def test_local_stage_receives_the_skill(self):
        from core.agents.inbox_triage import _categorize_thread

        seen: list[tuple[str, str]] = []

        async def _mock_llm(messages, **kwargs):
            seen.append((kwargs["stage"], messages[0]["content"]))
            return (
                '{"category":"personal","summary":"hi","action":"keep",'
                '"utility":"correspondence","confidence":0.95}'
            )

        with patch(
            "core.utils.internal_llm.call_internal_llm",
            new_callable=AsyncMock,
            side_effect=_mock_llm,
        ):
            short = await _categorize_thread("host", [_msg("host critical", "disk full")])
            await _categorize_thread("note", [_msg("Note", "x" * 900)])

        assert short["action"] == "keep"
        assert short["band"] == "local-small"
        assert [stage for stage, _prompt in seen] == ["inbox_triage", "inbox_triage"]
        assert "Return one JSON object" in seen[0][1]
        assert "send_gmail_message" not in seen[0][1]
        assert "delete-mail-message" not in seen[0][1]

    @pytest.mark.asyncio
    async def test_a_short_reply_climbs_to_the_frontier_model(self, monkeypatch):
        import config
        from core.agents.inbox_triage import _categorize_thread

        monkeypatch.setattr(config, "CERID_ENVIRONMENT_PROFILE", "hybrid", raising=False)
        answers = iter((
            '{"category":"actionable","summary":"a","action":"keep","utility":"correspondence","confidence":0.4}',
            '{"category":"personal","summary":"b","action":"keep","utility":"correspondence","confidence":0.5}',
            '{"category":"urgent","summary":"c","action":"keep","utility":"correspondence","confidence":0.92}',
        ))
        seen: list[str] = []

        async def _mock_llm(messages, **kwargs):
            del messages
            seen.append(kwargs["stage"])
            return next(answers)

        with patch(
            "core.utils.internal_llm.call_internal_llm",
            new_callable=AsyncMock,
            side_effect=_mock_llm,
        ):
            result = await _categorize_thread("note", [_msg("Note", "please look")])

        assert seen == ["inbox_triage", "inbox_triage_review", "inbox_triage_escalate"]
        assert result["category"] == "urgent"
        assert result["band"] == "cloud"
        assert result["model"] == "inbox_triage_escalate"

    @pytest.mark.asyncio
    async def test_local_only_stops_before_the_frontier_model(self, monkeypatch):
        import config
        from core.agents.inbox_triage import _categorize_thread

        monkeypatch.setattr(config, "CERID_ENVIRONMENT_PROFILE", "local-only", raising=False)
        answers = iter((
            '{"category":"actionable","summary":"a","action":"keep","utility":"correspondence","confidence":0.4}',
            '{"category":"personal","summary":"b","action":"keep","utility":"correspondence","confidence":0.5}',
        ))
        seen: list[str] = []

        async def _mock_llm(messages, **kwargs):
            del messages
            seen.append(kwargs["stage"])
            return next(answers)

        with patch(
            "core.utils.internal_llm.call_internal_llm",
            new_callable=AsyncMock,
            side_effect=_mock_llm,
        ):
            result = await _categorize_thread("note", [_msg("Note", "please look")])

        assert seen == ["inbox_triage", "inbox_triage_review"]
        assert result["category"] == "personal"
        assert result["band"] == "needs_review"
        assert result["model"] == "inbox_triage_review"

    @pytest.mark.asyncio
    async def test_a_failed_small_model_climbs_to_the_heavy_local_model(self, monkeypatch):
        import config
        from core.agents.inbox_triage import _categorize_thread

        monkeypatch.setattr(config, "CERID_ENVIRONMENT_PROFILE", "hybrid", raising=False)
        seen: list[str] = []

        async def _mock_llm(messages, **kwargs):
            del messages
            seen.append(kwargs["stage"])
            if kwargs["stage"] == "inbox_triage":
                raise RuntimeError("small slot down")
            return (
                '{"category":"personal","summary":"hi","action":"keep",'
                '"utility":"correspondence","confidence":0.91}'
            )

        with patch(
            "core.utils.internal_llm.call_internal_llm",
            new_callable=AsyncMock,
            side_effect=_mock_llm,
        ):
            result = await _categorize_thread("note", [_msg("Note", "please look")])

        assert seen == ["inbox_triage", "inbox_triage", "inbox_triage_review"]
        assert result["band"] == "local-chat"
        assert result["model"] == "inbox_triage_review"
        assert result["category"] == "personal"

    @pytest.mark.asyncio
    async def test_a_failed_small_reply_is_retried_on_the_same_stage(self, monkeypatch):
        import config
        from core.agents.inbox_triage import _categorize_thread

        monkeypatch.setattr(config, "CERID_ENVIRONMENT_PROFILE", "hybrid", raising=False)
        answers = iter((
            '{"confidence": 1.0}',
            '{"category":"personal","summary":"hi","action":"keep","utility":"correspondence","confidence":0.95}',
        ))
        seen: list[str] = []

        async def _mock_llm(messages, **kwargs):
            del messages
            seen.append(kwargs["stage"])
            return next(answers)

        with patch(
            "core.utils.internal_llm.call_internal_llm",
            new_callable=AsyncMock,
            side_effect=_mock_llm,
        ):
            result = await _categorize_thread("note", [_msg("Note", "please look")])

        assert seen == ["inbox_triage", "inbox_triage"]
        assert result["band"] == "local-small"
        assert result["model"] == "inbox_triage"
        assert result["category"] == "personal"
        assert result["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_a_card_shaped_reply_is_repaired_on_the_small_stage(self, monkeypatch):
        import config
        from core.agents.inbox_triage import _categorize_thread

        monkeypatch.setattr(config, "CERID_ENVIRONMENT_PROFILE", "hybrid", raising=False)
        seen: list[str] = []

        async def _mock_llm(messages, **kwargs):
            seen.append(kwargs["stage"])
            content = messages[0]["content"]
            if "No other keys" in content:
                return (
                    '{"category":"personal","confidence":0.95}'
                )
            return '{"payee":"northwind","amount":"40","currency":"USD"}'

        with patch(
            "core.utils.internal_llm.call_internal_llm",
            new_callable=AsyncMock,
            side_effect=_mock_llm,
        ):
            result = await _categorize_thread("note", [_msg("Note", "please look")])

        assert seen == ["inbox_triage", "inbox_triage", "inbox_triage"]
        assert "inbox_triage_review" not in seen
        assert "inbox_triage_escalate" not in seen
        assert result["band"] == "local-small"
        assert result["model"] == "inbox_triage"
        assert result["category"] == "personal"
        assert result["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_a_failed_category_repair_skips_the_heavy_rung(self, monkeypatch):
        import config
        from core.agents.inbox_triage import _categorize_thread

        monkeypatch.setattr(config, "CERID_ENVIRONMENT_PROFILE", "hybrid", raising=False)
        seen: list[str] = []

        async def _mock_llm(messages, **kwargs):
            seen.append(kwargs["stage"])
            if kwargs["stage"] == "inbox_triage_escalate":
                return (
                    '{"category":"urgent","summary":"c","action":"keep",'
                    '"utility":"correspondence","confidence":0.92}'
                )
            return '{"amount":"12","currency":"USD","payee":"northwind"}'

        with patch(
            "core.utils.internal_llm.call_internal_llm",
            new_callable=AsyncMock,
            side_effect=_mock_llm,
        ):
            result = await _categorize_thread("note", [_msg("Note", "please look")])

        assert seen == [
            "inbox_triage",
            "inbox_triage",
            "inbox_triage",
            "inbox_triage_escalate",
        ]
        assert result["band"] == "cloud"
        assert result["model"] == "inbox_triage_escalate"
        assert result["category"] == "urgent"

    @pytest.mark.asyncio
    async def test_a_short_category_repair_climbs_to_the_frontier(self, monkeypatch):
        import config
        from core.agents.inbox_triage import _categorize_thread

        monkeypatch.setattr(config, "CERID_ENVIRONMENT_PROFILE", "hybrid", raising=False)
        seen: list[str] = []

        async def _mock_llm(messages, **kwargs):
            content = messages[0]["content"]
            seen.append(kwargs["stage"])
            if "No other keys" in content:
                return '{"category":"actionable","confidence":0.4}'
            if kwargs["stage"] == "inbox_triage_escalate":
                return (
                    '{"category":"personal","summary":"b","action":"keep",'
                    '"utility":"correspondence","confidence":0.91}'
                )
            return '{"payee":"northwind"}'

        with patch(
            "core.utils.internal_llm.call_internal_llm",
            new_callable=AsyncMock,
            side_effect=_mock_llm,
        ):
            result = await _categorize_thread("note", [_msg("Note", "please look")])

        assert seen == [
            "inbox_triage",
            "inbox_triage",
            "inbox_triage",
            "inbox_triage_escalate",
        ]
        assert result["category"] == "personal"
        assert result["band"] == "cloud"

    @pytest.mark.asyncio
    async def test_local_only_category_repair_does_not_call_the_frontier(self, monkeypatch):
        import config
        from core.agents.inbox_triage import _categorize_thread

        monkeypatch.setattr(config, "CERID_ENVIRONMENT_PROFILE", "local-only", raising=False)
        seen: list[str] = []

        async def _mock_llm(messages, **kwargs):
            seen.append(kwargs["stage"])
            content = messages[0]["content"]
            if "No other keys" in content:
                return '{"category":"newsletter","confidence":0.5}'
            return '{"payee":"northwind"}'

        with patch(
            "core.utils.internal_llm.call_internal_llm",
            new_callable=AsyncMock,
            side_effect=_mock_llm,
        ):
            result = await _categorize_thread("note", [_msg("Note", "please look")])

        assert seen == ["inbox_triage", "inbox_triage", "inbox_triage"]
        assert result["category"] == "newsletter"
        assert result["confidence"] == 0.5
        assert result["band"] == "needs_review"
        assert result["model"] == "inbox_triage"

    @pytest.mark.asyncio
    async def test_apple_mail_is_a_triage_source(self):
        from core.agents.inbox_triage import triage_inboxes

        apple = _StubSource("apple_mail", [_msg("Hello", "see you", metadata=_provider("apple-1"))])

        async def _mock_llm(messages, **kwargs):
            return '{"category":"personal","summary":"hi","suggested_action":"reply","confidence":0.95}'

        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.data_sources.base.registry.get", side_effect=lambda name: apple if name == "apple_mail" else None),
            patch("core.utils.internal_llm.call_internal_llm", new_callable=AsyncMock, side_effect=_mock_llm),
        ):
            result = await triage_inboxes(persist=False)

        assert result.sources_queried == ["apple_mail"]
        assert result.threads[0].thread_id == "apple-1"
        assert result.threads[0].writable is True

    @pytest.mark.asyncio
    async def test_subject_only_thread_is_not_ingested(self):
        from core.agents.inbox_triage import triage_inboxes

        gmail_src = _StubSource("gmail", [_msg("test", "body")])
        client = _RecordingClient()

        async def _mock_llm(messages, **kwargs):
            return '{"category":"actionable","summary":"x","suggested_action":"y","confidence":0.95}'

        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.data_sources.base.registry.get", side_effect=lambda name: gmail_src if name == "gmail" else None),
            patch("core.utils.internal_llm.call_internal_llm", new_callable=AsyncMock, side_effect=_mock_llm),
            patch("httpx.AsyncClient", return_value=client),
        ):
            result = await triage_inboxes(persist=True, mcp_base_url="http://test")

        assert result.threads[0].writable is False
        assert result.threads[0].artifact_id is None
        assert client.posts == []

    @pytest.mark.asyncio
    async def test_utility_none_stores_no_body(self):
        from core.agents.inbox_triage import triage_inboxes

        gmail_src = _StubSource("gmail", [_msg("Sale", "unsubscribe", metadata=_provider())])
        client = _RecordingClient()

        async def _mock_llm(messages, **kwargs):
            return '{"category":"promo","summary":"ad","utility":"none","confidence":0.95}'

        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.data_sources.base.registry.get", side_effect=lambda name: gmail_src if name == "gmail" else None),
            patch("core.utils.internal_llm.call_internal_llm", new_callable=AsyncMock, side_effect=_mock_llm),
            patch("httpx.AsyncClient", return_value=client),
        ):
            result = await triage_inboxes(persist=True, mcp_base_url="http://test")

        assert result.threads[0].utility == "none"
        assert result.threads[0].artifact_id is None
        assert client.posts == []

    @pytest.mark.asyncio
    async def test_financial_card_goes_to_finance_and_leaves_the_body_out(self):
        from core.agents.inbox_triage import triage_inboxes

        body = "Your bill is ready. Amount due $42.10 on 2026-10-01. Patio work account 999."
        gmail_src = _StubSource("gmail", [_msg("City Power", body, metadata=_provider("bill-1"))])
        client = _RecordingClient()

        async def _mock_llm(messages, **kwargs):
            return '{"category":"actionable","summary":"power bill","confidence":0.95}'

        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.data_sources.base.registry.get", side_effect=lambda name: gmail_src if name == "gmail" else None),
            patch("core.utils.internal_llm.call_internal_llm", new_callable=AsyncMock, side_effect=_mock_llm),
            patch("httpx.AsyncClient", return_value=client),
        ):
            result = await triage_inboxes(persist=True, mcp_base_url="http://test")

        domains = [post["domain"] for post in client.posts]
        assert domains == ["finance", "inbox"]
        card = client.posts[0]["content"]
        assert "42.10" in card
        assert "2026-10-01" in card
        assert "Patio" not in card
        assert "999" not in card
        assert "Marker: amount due" in card
        assert client.posts[0]["metadata"]["record_type"] == "mail_financial_card"
        assert client.posts[1]["content"] == "City Power — finance card"
        assert "Patio" not in client.posts[1]["content"]
        thread = result.threads[0]
        assert thread.utility == "financial"
        assert thread.finance_artifact_id == "art:finance"
        assert thread.artifact_id == "art:inbox"

    @pytest.mark.asyncio
    async def test_unwired_route_does_not_ingest(self):
        import core.agents.inbox_triage as agent
        from core.agents.inbox_triage import triage_inboxes

        agent._rag_route = None
        gmail_src = _StubSource("gmail", [_msg("test", "body", metadata=_provider())])
        client = _RecordingClient()

        async def _mock_llm(messages, **kwargs):
            return '{"category":"actionable","summary":"x","suggested_action":"y","confidence":0.95}'

        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.data_sources.base.registry.get", side_effect=lambda name: gmail_src if name == "gmail" else None),
            patch("core.utils.internal_llm.call_internal_llm", new_callable=AsyncMock, side_effect=_mock_llm),
            patch("httpx.AsyncClient", return_value=client),
        ):
            result = await triage_inboxes(persist=True, mcp_base_url="http://test")

        assert result.threads[0].artifact_id is None
        assert client.posts == []


class _RecordingClient:
    def __init__(self) -> None:
        self.posts: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, json, headers):
        self.posts.append(json)

        class _Resp:
            status_code = 200

            def json(self):
                return {"artifact_id": f"art:{json['domain']}"}

        return _Resp()
