# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 post-audit M4 tail — R13 private-mode off marker, R15 claim key, R18 no double memory."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


def test_reset_private_mode_sets_explicit_zero() -> None:
    """R13: reset must SET '0', not delete the key (seed would re-apply env)."""
    src = (Path(__file__).resolve().parents[1] / "app/routers/settings.py").read_text(
        encoding="utf-8",
    )
    # The reset handler should set "0" not only delete
    assert 'redis.set(_PRIVATE_MODE_KEY, "0")' in src or "redis.set(_PRIVATE_MODE_KEY, '0')" in src
    assert "redis.delete(_PRIVATE_MODE_KEY)" not in src.split("async def reset_private_mode")[1].split(
        "async def ",
    )[0]


def test_fallback_result_includes_claim() -> None:
    """R15: _fallback_result source contains claim key assignment."""
    src = (
        Path(__file__).resolve().parents[1]
        / "core/agents/hallucination/streaming.py"
    ).read_text(encoding="utf-8")
    assert '"claim": claim_text' in src or "'claim': claim_text" in src


def test_delete_conversation_gates_private_blocks() -> None:
    """CR-061: DELETE /conversations must call private_blocks."""
    src = (Path(__file__).resolve().parents[1] / "app/routers/user_state.py").read_text(
        encoding="utf-8",
    )
    assert "private_blocks(1)" in src
    assert "remove_conversation" in src


def test_main_reprojects_local_urls() -> None:
    """R10: boot path projects quenchforge/ollama URLs from persisted config."""
    src = (Path(__file__).resolve().parents[1] / "app/main.py").read_text(encoding="utf-8")
    assert "QUENCHFORGE_URL" in src
    assert "OLLAMA_URL" in src
    assert "project_byok_env" in src


@pytest.mark.asyncio
async def test_smart_mode_disables_agent_query_memory_when_memory_task_runs() -> None:
    """R18: agent_query gets memory_enabled=False when memory_task is active."""
    from app.agents.retrieval_orchestrator import orchestrated_query

    captured: dict = {}

    async def _fake_aq(**kwargs):
        captured.update(kwargs)
        return {
            "results": [{"content": "kb", "relevance": 0.9, "source_type": "kb"}],
            "sources": [],
            "context": "c",
            "confidence": 0.9,
        }

    async def _fake_mem(**kwargs):
        return [{"text": "mem", "adjusted_score": 0.8}]

    with (
        patch("core.agents.query_agent.agent_query", new=AsyncMock(side_effect=_fake_aq)),
        patch(
            "app.agents.retrieval_orchestrator._recall_with_timeout",
            new=AsyncMock(side_effect=_fake_mem),
        ),
        patch(
            "app.agents.retrieval_orchestrator._query_external_sources",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await orchestrated_query(
            query="q",
            rag_mode="smart",
            context_sources={"kb": True, "memory": True, "external": False},
        )

    assert captured.get("memory_enabled") is False
    mems = [r for r in result["results"] if r.get("source_type") == "memory"]
    assert len(mems) == 1
