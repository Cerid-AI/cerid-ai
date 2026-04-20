# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the unified retrieval orchestrator (agents/retrieval_orchestrator.py)."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.retrieval_orchestrator import (
    _format_memory_context,
    orchestrated_query,
)

# _apply_source_config was extracted to the BSL-1.1 custom-rag plugin.
# Import it directly for unit testing. Walk up from tests/ to find repo root
# regardless of whether we're running natively or inside a Docker container.
_test_dir = Path(__file__).resolve().parent
_repo_root = _test_dir
for _ in range(10):  # walk up at most 10 levels to find plugins/
    if (_repo_root / "plugins" / "custom-rag" / "plugin.py").exists():
        break
    _repo_root = _repo_root.parent
else:
    _repo_root = None  # type: ignore[assignment]

_apply_source_config = None
if _repo_root is not None:
    _plugin_path = _repo_root / "plugins" / "custom-rag" / "plugin.py"
    _spec = importlib.util.spec_from_file_location("cerid_plugin_custom_rag_test", str(_plugin_path))
    if _spec and _spec.loader:
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _apply_source_config = _mod.apply_source_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _fake_kb_result(**overrides):
    base = {
        "query": "test query",
        "domains_queried": ["general"],
        "total_results": 2,
        "deduplicated_results": 2,
        "results": [
            {"content": "KB doc 1", "relevance": 0.9, "artifact_id": "a1", "filename": "doc1.pdf",
             "domain": "general", "chunk_index": 0, "collection": "default", "ingested_at": "2026-01-01"},
            {"content": "External wiki", "relevance": 0.7, "artifact_id": "ext1", "filename": "Wikipedia",
             "domain": "external", "chunk_index": 0, "collection": "default", "ingested_at": "2026-01-01",
             "source_url": "https://en.wikipedia.org/wiki/Test", "source_type": "external"},
        ],
        "confidence": 0.85,
        "reranking_used": True,
        "execution_time_ms": 120,
        "timestamp": "2026-01-01T00:00:00",
        "context": "KB doc 1 content here",
    }
    base.update(overrides)
    return base


def _fake_memory_results():
    return [
        {
            "text": "User prefers Python over JavaScript",
            "adjusted_score": 0.91,
            "memory_type": "preference",
            "age_days": 5.0,
            "summary": "Prefers Python",
            "memory_id": "mem-1",
            "source_authority": 0.8,
            "base_similarity": 0.88,
            "access_count": 3,
        },
        {
            "text": "Project uses PostgreSQL as primary database",
            "adjusted_score": 0.85,
            "memory_type": "decision",
            "age_days": 12.0,
            "summary": "Uses PostgreSQL",
            "memory_id": "mem-2",
            "source_authority": 0.7,
            "base_similarity": 0.82,
            "access_count": 1,
        },
    ]


# ---------------------------------------------------------------------------
# Manual mode tests
# ---------------------------------------------------------------------------

class TestManualMode:
    """Manual mode is a pure pass-through to agent_query()."""

    def test_manual_mode_passthrough(self):
        """Manual mode should call agent_query() and return its result unchanged."""
        fake_result = _fake_kb_result()

        with patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq:
            mock_aq.return_value = fake_result
            result = _run(orchestrated_query(query="test", rag_mode="manual"))

        mock_aq.assert_awaited_once()
        assert result == fake_result
        assert "source_breakdown" not in result

    def test_manual_mode_passes_all_kwargs(self):
        """Manual mode should forward all kwargs to agent_query()."""
        with patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq:
            mock_aq.return_value = _fake_kb_result()
            _run(orchestrated_query(
                query="test",
                rag_mode="manual",
                domains=["coding"],
                top_k=5,
                use_reranking=False,
            ))

        call_kwargs = mock_aq.call_args.kwargs
        assert call_kwargs["domains"] == ["coding"]
        assert call_kwargs["top_k"] == 5
        assert call_kwargs["use_reranking"] is False


# ---------------------------------------------------------------------------
# Smart mode tests
# ---------------------------------------------------------------------------

class TestSmartMode:
    """Smart mode runs KB + memory in parallel and returns source_breakdown."""

    def test_smart_mode_parallel_execution(self):
        """Smart mode should call both agent_query and recall_memories."""
        with (
            patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq,
            patch("app.agents.retrieval_orchestrator._recall_with_timeout", new_callable=AsyncMock) as mock_recall,
        ):
            mock_aq.return_value = _fake_kb_result()
            mock_recall.return_value = _fake_memory_results()

            result = _run(orchestrated_query(query="test", rag_mode="smart"))

        mock_aq.assert_awaited_once()
        mock_recall.assert_awaited_once()
        assert "source_breakdown" in result
        assert result["rag_mode"] == "smart"

    def test_smart_mode_source_breakdown_structure(self):
        """source_breakdown should have kb, memory, and external keys."""
        with (
            patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq,
            patch("app.agents.retrieval_orchestrator._recall_with_timeout", new_callable=AsyncMock) as mock_recall,
        ):
            mock_aq.return_value = _fake_kb_result()
            mock_recall.return_value = _fake_memory_results()

            result = _run(orchestrated_query(query="test", rag_mode="smart"))

        sb = result["source_breakdown"]
        assert "kb" in sb
        assert "memory" in sb
        assert "external" in sb

    def test_smart_mode_separates_external_from_kb(self):
        """External results (with source_url) should be in external, not kb."""
        with (
            patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq,
            patch("app.agents.retrieval_orchestrator._recall_with_timeout", new_callable=AsyncMock) as mock_recall,
            patch("app.agents.retrieval_orchestrator._query_external_sources", new_callable=AsyncMock, return_value=[]),
        ):
            mock_aq.return_value = _fake_kb_result()
            mock_recall.return_value = []

            result = _run(orchestrated_query(query="test", rag_mode="smart"))

        sb = result["source_breakdown"]
        assert len(sb["kb"]) == 1
        assert sb["kb"][0]["filename"] == "doc1.pdf"
        assert len(sb["external"]) == 1
        assert sb["external"][0]["source_url"] == "https://en.wikipedia.org/wiki/Test"

    def test_smart_mode_memory_sources_formatted(self):
        """Memory results should be formatted with source_type: memory."""
        with (
            patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq,
            patch("app.agents.retrieval_orchestrator._recall_with_timeout", new_callable=AsyncMock) as mock_recall,
        ):
            mock_aq.return_value = _fake_kb_result()
            mock_recall.return_value = _fake_memory_results()

            result = _run(orchestrated_query(query="test", rag_mode="smart"))

        memories = result["source_breakdown"]["memory"]
        assert len(memories) == 2
        assert all(m["source_type"] == "memory" for m in memories)
        assert memories[0]["memory_type"] == "preference"
        assert memories[1]["memory_type"] == "decision"

    def test_smart_mode_appends_memory_context(self):
        """Memory results should be appended to the context string."""
        with (
            patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq,
            patch("app.agents.retrieval_orchestrator._recall_with_timeout", new_callable=AsyncMock) as mock_recall,
        ):
            mock_aq.return_value = _fake_kb_result()
            mock_recall.return_value = _fake_memory_results()

            result = _run(orchestrated_query(query="test", rag_mode="smart"))

        assert "[Memory Context]" in result["context"]
        assert "Prefers Python" in result["context"]

    def test_smart_mode_no_memory_no_context_append(self):
        """When no memories match, context should remain unchanged."""
        with (
            patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq,
            patch("app.agents.retrieval_orchestrator._recall_with_timeout", new_callable=AsyncMock) as mock_recall,
        ):
            original = _fake_kb_result()
            mock_aq.return_value = original
            mock_recall.return_value = []

            result = _run(orchestrated_query(query="test", rag_mode="smart"))

        assert "[Memory Context]" not in result.get("context", "")


# ---------------------------------------------------------------------------
# Custom smart mode tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_apply_source_config is None, reason="custom-rag plugin not found (Docker path)")
class TestCustomSmartMode:
    """Custom smart mode applies source_config weights and toggles."""

    def test_custom_smart_disables_sources(self):
        """source_config can disable individual source types."""
        with (
            patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq,
            patch("app.agents.retrieval_orchestrator._recall_with_timeout", new_callable=AsyncMock) as mock_recall,
            patch("app.agents.retrieval_orchestrator._custom_rag_fn", _apply_source_config),
        ):
            mock_aq.return_value = _fake_kb_result()
            mock_recall.return_value = _fake_memory_results()

            result = _run(orchestrated_query(
                query="test",
                rag_mode="custom_smart",
                source_config={"memory_enabled": False, "kb_enabled": True, "external_enabled": True},
            ))

        sb = result["source_breakdown"]
        assert len(sb["memory"]) == 0
        assert len(sb["kb"]) > 0

    def test_custom_smart_weight_scaling(self):
        """source_config weights should scale relevance scores."""
        kb = [{"content": "doc", "relevance": 0.8}]
        memory = [{"content": "mem", "relevance": 0.9, "memory_type": "empirical"}]
        external = [{"content": "ext", "relevance": 0.7}]

        config = {
            "kb_enabled": True, "memory_enabled": True, "external_enabled": True,
            "kb_weight": 0.5, "memory_weight": 2.0, "external_weight": 1.0,
        }

        kb_out, mem_out, ext_out = _apply_source_config(kb, memory, external, config)
        assert kb_out[0]["relevance"] == pytest.approx(0.4)  # 0.8 * 0.5
        assert mem_out[0]["relevance"] == pytest.approx(1.8)  # 0.9 * 2.0
        assert ext_out[0]["relevance"] == pytest.approx(0.7)  # 0.7 * 1.0

    def test_custom_smart_memory_type_filter(self):
        """memory_types filter should only keep specified types."""
        memory = [
            {"content": "pref", "relevance": 0.9, "memory_type": "preference"},
            {"content": "dec", "relevance": 0.8, "memory_type": "decision"},
            {"content": "emp", "relevance": 0.7, "memory_type": "empirical"},
        ]

        config = {
            "kb_enabled": True, "memory_enabled": True, "external_enabled": True,
            "memory_types": ["preference", "empirical"],
        }

        _, mem_out, _ = _apply_source_config([], memory, [], config)
        assert len(mem_out) == 2
        types = {m["memory_type"] for m in mem_out}
        assert types == {"preference", "empirical"}


# ---------------------------------------------------------------------------
# Memory recall timeout tests
# ---------------------------------------------------------------------------

class TestMemoryRecallTimeout:
    """Memory recall should gracefully handle timeouts and errors."""

    def test_timeout_returns_empty(self):
        """Timeout should return empty list, not raise."""
        from app.agents.retrieval_orchestrator import _recall_with_timeout

        async def slow_recall(**kwargs):
            await asyncio.sleep(10)
            return [{"text": "never returned"}]

        with patch("app.agents.memory.recall_memories", side_effect=slow_recall):
            result = _run(_recall_with_timeout(
                query="test", chroma_client=None, neo4j_driver=None,
                top_k=5, min_score=0.4, timeout_ms=50,
            ))

        assert result == []

    def test_error_returns_empty(self):
        """Exception in recall should return empty list."""
        from app.agents.retrieval_orchestrator import _recall_with_timeout

        with patch("app.agents.memory.recall_memories", new_callable=AsyncMock) as mock:
            mock.side_effect = RuntimeError("Neo4j down")
            result = _run(_recall_with_timeout(
                query="test", chroma_client=None, neo4j_driver=None,
                top_k=5, min_score=0.4, timeout_ms=200,
            ))

        assert result == []


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestHelpers:
    """Tests for _format_memory_context and custom-rag plugin's apply_source_config."""

    def test_format_memory_context(self):
        """Memory context should have header and formatted entries."""
        sources = [
            {"summary": "Prefers Python", "memory_type": "preference", "relevance": 0.91, "content": "..."},
            {"summary": "", "memory_type": "decision", "relevance": 0.85, "content": "Uses PostgreSQL for DB"},
        ]
        result = _format_memory_context(sources)
        assert result.startswith("[Memory Context]")
        assert "preference" in result
        assert "Prefers Python" in result
        assert "Uses PostgreSQL for DB" in result  # Falls back to content[:80]

    @pytest.mark.skipif(_apply_source_config is None, reason="custom-rag plugin not found")
    def test_apply_source_config_all_disabled(self):
        """Disabling all sources should return empty lists."""
        config = {"kb_enabled": False, "memory_enabled": False, "external_enabled": False}
        kb, mem, ext = _apply_source_config(
            [{"relevance": 0.9}], [{"relevance": 0.8, "memory_type": "empirical"}], [{"relevance": 0.7}],
            config,
        )
        assert kb == []
        assert mem == []
        assert ext == []

    @pytest.mark.skipif(_apply_source_config is None, reason="custom-rag plugin not found")
    def test_apply_source_config_no_relevance_key(self):
        """Items without 'relevance' key should not crash weight application."""
        config = {"kb_enabled": True, "memory_enabled": True, "external_enabled": True,
                  "kb_weight": 2.0, "memory_weight": 1.0, "external_weight": 1.0}
        kb, _, _ = _apply_source_config(
            [{"content": "no relevance field"}], [], [], config,
        )
        assert "relevance" not in kb[0]  # Should not add relevance key
