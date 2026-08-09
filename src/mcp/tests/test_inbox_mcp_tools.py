# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for pkb_inbox_triage + pkb_inbox_filter MCP tools — Phase J Day 3."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.mcp_tools.inbox import pkb_inbox_filter, pkb_inbox_triage


class TestPkbInboxTriage:
    @pytest.mark.asyncio
    async def test_delegates_to_agent(self):
        from core.agents.inbox_triage import TriagedThread, TriageResult

        fake = TriageResult(
            threads=[
                TriagedThread(
                    thread_id="t1", source="gmail", participants=["a@x"],
                    subject="x", message_count=1, latest_at="0",
                    category="urgent", summary="s", suggested_action="reply",
                ),
            ],
            by_category={"urgent": 1},
            sources_queried=["gmail"],
        )

        with patch(
            "core.agents.inbox_triage.triage_inboxes",
            new_callable=AsyncMock,
            return_value=fake,
        ) as mock_agent:
            result = await pkb_inbox_triage(query="is:unread", max_results_per_source=10)

        mock_agent.assert_awaited_once_with(
            query="is:unread",
            max_results_per_source=10,
            persist=True,
        )
        assert result["by_category"]["urgent"] == 1
        assert result["threads"][0]["category"] == "urgent"

    @pytest.mark.asyncio
    async def test_persist_false_passed_through(self):
        from core.agents.inbox_triage import TriageResult

        with patch(
            "core.agents.inbox_triage.triage_inboxes",
            new_callable=AsyncMock,
            return_value=TriageResult(),
        ) as mock_agent:
            await pkb_inbox_triage(persist=False)
        kwargs = mock_agent.await_args.kwargs
        assert kwargs["persist"] is False


class TestPkbInboxFilter:
    @pytest.mark.asyncio
    async def test_skips_when_feature_off(self):
        with patch("config.features.is_feature_enabled", return_value=False):
            result = await pkb_inbox_filter(category="urgent")
        assert result["threads"] == []
        assert result["total"] == 0
        assert result["filter_applied"]["feature_gated"] is True

    @pytest.mark.asyncio
    async def test_filters_by_category(self):
        fake_artifacts = [
            {
                "id": "art:1", "filename": "Q3 plan",
                "tags": {
                    "category": "urgent", "origin_source": "gmail",
                    "summary": "fire", "suggested_action": "reply",
                    "thread_id": "t1", "subject": "Q3 plan",
                },
            },
            {
                "id": "art:2", "filename": "Newsletter",
                "tags": {
                    "category": "newsletter", "origin_source": "outlook",
                    "summary": "weekly", "suggested_action": "skim",
                    "thread_id": "t2", "subject": "Newsletter",
                },
            },
        ]
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.deps.get_neo4j", return_value=object()),
            patch(
                "app.mcp_tools.inbox._list_inbox_artifacts",
                new_callable=AsyncMock,
                return_value=fake_artifacts,
            ),
        ):
            result = await pkb_inbox_filter(category="urgent")

        assert result["total"] == 1
        assert result["threads"][0]["category"] == "urgent"
        assert result["threads"][0]["source"] == "gmail"

    @pytest.mark.asyncio
    async def test_filters_by_source(self):
        fake_artifacts = [
            {"id": "1", "tags": {"origin_source": "gmail", "category": "urgent"}},
            {"id": "2", "tags": {"origin_source": "outlook", "category": "urgent"}},
        ]
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.deps.get_neo4j", return_value=object()),
            patch(
                "app.mcp_tools.inbox._list_inbox_artifacts",
                new_callable=AsyncMock,
                return_value=fake_artifacts,
            ),
        ):
            result = await pkb_inbox_filter(source="gmail")

        assert result["total"] == 1
        assert result["threads"][0]["source"] == "gmail"

    @pytest.mark.asyncio
    async def test_handles_neo4j_failure_gracefully(self):
        with (
            patch("config.features.is_feature_enabled", return_value=True),
            patch("app.deps.get_neo4j", return_value=object()),
            patch(
                "app.mcp_tools.inbox._list_inbox_artifacts",
                new_callable=AsyncMock,
                side_effect=RuntimeError("neo4j down"),
            ),
        ):
            result = await pkb_inbox_filter(category="urgent")
        assert result["total"] == 0
        assert "error" in result["filter_applied"]


class TestToolRegistration:
    def test_both_tools_registered(self):
        # Importing the inbox module registers the tools
        from app.mcp_tools import inbox  # noqa: F401
        from app.tool_registry import TOOL_REGISTRY

        assert "pkb_inbox_triage" in TOOL_REGISTRY
        assert "pkb_inbox_filter" in TOOL_REGISTRY

    def test_triage_tool_marked_high_cost(self):
        from app.tool_registry import TOOL_REGISTRY
        meta = TOOL_REGISTRY["pkb_inbox_triage"]
        # Each tool's metadata exposes its cost_class (used for budget tracking).
        # The actual attribute name varies by registry version — accept either.
        cost = getattr(meta, "cost_class", None) or (
            meta.get("cost_class") if isinstance(meta, dict) else None
        )
        assert cost == "high"
