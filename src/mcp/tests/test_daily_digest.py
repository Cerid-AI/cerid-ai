# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for daily_digest agent — Phase K Day 1."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# ── snapshot builders ─────────────────────────────────────────────────

class TestSnapshotBuilders:
    def test_activity_snapshot_truncates_long_lines(self):
        from core.agents.daily_digest import _build_activity_snapshot
        artifacts = [
            {"domain": "notes", "filename": "Recipe", "summary": "x" * 1000},
        ]
        out = _build_activity_snapshot(artifacts)
        assert len(out) < 1000

    def test_activity_snapshot_includes_domain_tag(self):
        from core.agents.daily_digest import _build_activity_snapshot
        artifacts = [{"domain": "mail", "filename": "Q3 plan", "summary": "thoughts"}]
        out = _build_activity_snapshot(artifacts)
        assert "[mail]" in out
        assert "Q3 plan" in out

    def test_activity_snapshot_handles_missing_summary(self):
        from core.agents.daily_digest import _build_activity_snapshot
        artifacts = [{"domain": "notes", "filename": "Test"}]
        out = _build_activity_snapshot(artifacts)
        assert "Test" in out

    def test_flagged_snapshot_includes_score(self):
        from core.agents.daily_digest import _build_flagged_snapshot
        artifacts = [{"filename": "Bad note", "quality_score": 0.3}]
        out = _build_flagged_snapshot(artifacts)
        assert "0.30" in out
        assert "Bad note" in out

    def test_flagged_snapshot_empty(self):
        from core.agents.daily_digest import _build_flagged_snapshot
        assert "no quality alerts" in _build_flagged_snapshot([]).lower()

    def test_inbox_snapshot_renders_category(self):
        from core.agents.daily_digest import _build_inbox_snapshot
        artifacts = [{
            "filename": "x",
            "tags": {"category": "urgent", "subject": "Outage", "summary": "down"},
        }]
        out = _build_inbox_snapshot(artifacts)
        assert "[urgent]" in out
        assert "Outage" in out


# ── top-categories rollup ─────────────────────────────────────────────

class TestTopCategories:
    def test_counts_by_domain(self):
        from core.agents.daily_digest import _compute_top_categories
        artifacts = [
            {"domain": "notes"}, {"domain": "notes"}, {"domain": "mail"},
            {"domain": "meetings"}, {"domain": "notes"},
        ]
        result = _compute_top_categories(artifacts)
        assert result[0]["domain"] == "notes"
        assert result[0]["count"] == 3
        assert {r["domain"] for r in result} == {"notes", "mail", "meetings"}

    def test_caps_at_limit(self):
        from core.agents.daily_digest import _compute_top_categories
        artifacts = [{"domain": f"d{i}"} for i in range(10)]
        result = _compute_top_categories(artifacts, limit=3)
        assert len(result) == 3

    def test_missing_domain_falls_back_general(self):
        from core.agents.daily_digest import _compute_top_categories
        result = _compute_top_categories([{}, {"domain": None}])
        assert result[0]["domain"] == "general"
        assert result[0]["count"] == 2


# ── LLM response parser ──────────────────────────────────────────────

class TestLLMParser:
    def test_parses_dict_directly(self):
        from core.agents.daily_digest import _parse_llm_response
        assert _parse_llm_response({"action_items": ["x"]}) == {"action_items": ["x"]}

    def test_parses_clean_json_string(self):
        from core.agents.daily_digest import _parse_llm_response
        result = _parse_llm_response('{"action_items":["x"]}')
        assert result["action_items"] == ["x"]

    def test_parses_code_fenced(self):
        from core.agents.daily_digest import _parse_llm_response
        raw = '```json\n{"action_items":["x"]}\n```'
        result = _parse_llm_response(raw)
        assert result["action_items"] == ["x"]

    def test_returns_empty_when_unparseable(self):
        from core.agents.daily_digest import _parse_llm_response
        assert _parse_llm_response("not json at all") == {}
        assert _parse_llm_response(None) == {}


class TestCoerceSections:
    def test_drops_non_dict_entries(self):
        from core.agents.daily_digest import _coerce_sections
        result = _coerce_sections([
            {"title": "good", "body": "body"},
            "not a dict",
            42,
        ])
        assert len(result) == 1
        assert result[0].title == "good"

    def test_caps_at_5(self):
        from core.agents.daily_digest import _coerce_sections
        many = [{"title": f"t{i}", "body": "x"} for i in range(20)]
        assert len(_coerce_sections(many)) == 5

    def test_truncates_long_body(self):
        from core.agents.daily_digest import _coerce_sections
        result = _coerce_sections([{"title": "t", "body": "x" * 5000}])
        assert len(result[0].body) <= 600


# ── feature gate ──────────────────────────────────────────────────────

class TestFeatureGate:
    @pytest.mark.asyncio
    async def test_skipped_when_feature_off(self):
        from core.agents.daily_digest import generate_daily_digest
        with patch("config.features.is_feature_enabled", return_value=False):
            result = await generate_daily_digest(persist=False)
        assert result.skipped is True
        assert result.skip_reason == "feature_gated"

    @pytest.mark.asyncio
    async def test_skipped_when_neo4j_unavailable(self):
        from core.agents.daily_digest import generate_daily_digest
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.deps.get_neo4j", side_effect=RuntimeError("boom")),
        ):
            result = await generate_daily_digest(persist=False)
        assert result.skipped is True
        assert result.skip_reason == "neo4j_unavailable"


# ── end-to-end with mocked LLM ────────────────────────────────────────

class TestGenerateDailyDigest:
    @pytest.fixture(autouse=True)
    def _wire_digest_di(self):
        # core/ reads the graph accessor via DI now; wire the real adapter (the
        # same one app startup injects) so the app.deps.get_neo4j patches below
        # take effect. Reset after each test to avoid cross-test leakage.
        import core.agents.daily_digest as _m
        from app.agents_di import wire_daily_digest_di

        wire_daily_digest_di()
        yield
        _m._graph = None

    @pytest.mark.asyncio
    async def test_empty_window_returns_zero_activity_digest(self):
        """Zero artifacts → digest with empty sections but
        non-skipped status so user sees the 'nothing happened' signal."""
        from core.agents.daily_digest import generate_daily_digest

        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.deps.get_neo4j", return_value=object()),
            patch("core.agents.daily_digest._fetch_recent_artifacts", return_value=[]),
            patch("core.agents.daily_digest._fetch_flagged_artifacts", return_value=[]),
            patch("core.agents.daily_digest._fetch_inbox_urgent", return_value=[]),
        ):
            result = await generate_daily_digest(persist=False)

        assert result.skipped is False
        assert result.artifact_count == 0
        assert result.top_categories == []
        # LLM never called (no signal to send)

    @pytest.mark.asyncio
    async def test_full_pipeline_with_mocked_llm(self):
        from core.agents.daily_digest import generate_daily_digest

        artifacts = [
            {"domain": "notes", "filename": "Recipe", "summary": "sourdough"},
            {"domain": "meetings", "filename": "Standup", "summary": "team sync"},
        ]
        flagged = [{"filename": "Stub doc", "quality_score": 0.3}]
        inbox = [{
            "filename": "x", "tags": {"category": "urgent", "subject": "Outage", "summary": "down"},
        }]

        llm_response = (
            '{"top_categories":['
            '{"domain":"notes","count":1,"highlight":"new recipe"}],'
            '"key_threads":[{"title":"Recipe","body":"new sourdough recipe"}],'
            '"urgent":[{"title":"Outage","body":"production down"}],'
            '"action_items":["reply to outage thread","try the sourdough recipe"],'
            '"quality_alerts":[{"title":"Stub doc","body":"missing summary"}]}'
        )

        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.deps.get_neo4j", return_value=object()),
            patch("core.agents.daily_digest._fetch_recent_artifacts", return_value=artifacts),
            patch("core.agents.daily_digest._fetch_flagged_artifacts", return_value=flagged),
            patch("core.agents.daily_digest._fetch_inbox_urgent", return_value=inbox),
            patch("core.utils.internal_llm.call_internal_llm",
                  new_callable=AsyncMock, return_value=llm_response),
        ):
            result = await generate_daily_digest(persist=False)

        assert result.skipped is False
        assert result.artifact_count == 2
        assert result.flagged_count == 1
        assert result.inbox_urgent_count == 1
        # Top categories use deterministic counting (not LLM data)
        assert any(c["domain"] == "notes" for c in result.top_categories)
        # LLM-supplied highlight propagated
        notes_cat = next(c for c in result.top_categories if c["domain"] == "notes")
        assert notes_cat["highlight"] == "new recipe"
        # Sections populated
        assert any("Recipe" in s.title for s in result.key_threads)
        assert any("Outage" in s.title for s in result.urgent)
        assert "reply to outage thread" in result.action_items
        assert any("Stub doc" in s.title for s in result.quality_alerts)

    @pytest.mark.asyncio
    async def test_handles_llm_failure_gracefully(self):
        """If the LLM call throws, the digest still returns with
        deterministic categories but empty narrative sections."""
        from core.agents.daily_digest import generate_daily_digest

        artifacts = [{"domain": "notes", "filename": "n", "summary": "s"}]

        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.deps.get_neo4j", return_value=object()),
            patch("core.agents.daily_digest._fetch_recent_artifacts", return_value=artifacts),
            patch("core.agents.daily_digest._fetch_flagged_artifacts", return_value=[]),
            patch("core.agents.daily_digest._fetch_inbox_urgent", return_value=[]),
            patch("core.utils.internal_llm.call_internal_llm",
                  new_callable=AsyncMock, side_effect=RuntimeError("LLM down")),
        ):
            result = await generate_daily_digest(persist=False)

        # No skip — partial success path
        assert result.skipped is False
        # Deterministic data survives
        assert any(c["domain"] == "notes" for c in result.top_categories)
        # Narrative sections empty (LLM didn't produce them)
        assert result.key_threads == []
        assert result.action_items == []
