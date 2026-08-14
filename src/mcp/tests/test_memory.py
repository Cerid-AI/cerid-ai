# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for memory extraction agent (Phase 7C)."""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Pre-seed heavy modules that extract_and_store_memories imports lazily
# so @patch can target them without triggering real imports.
if "routers.ingestion" not in sys.modules:
    _stub = ModuleType("routers.ingestion")
    _stub.ingest_content = None  # type: ignore[attr-defined]
    _stub.ingest_batch = None  # type: ignore[attr-defined]
    _stub.router = MagicMock()  # type: ignore[attr-defined]
    sys.modules["routers.ingestion"] = _stub
    # Also register as attribute on the parent package so _dot_lookup works.
    if "routers" not in sys.modules:
        sys.modules["routers"] = ModuleType("routers")
    _routers_pkg = sys.modules["routers"]
    _routers_pkg.ingestion = _stub  # type: ignore[attr-defined]

from app.agents.memory import (
    archive_old_memories,
    extract_and_store_memories,
    extract_memories,
)


class TestExtractMemories:
    """Test memory extraction via LLM."""

    @pytest.mark.asyncio
    async def test_short_response_returns_empty(self):
        """Responses below minimum length should return no memories."""
        result = await extract_memories("short", "conv-123")
        assert result == []

    @pytest.mark.asyncio
    @patch("core.agents.memory.call_internal_llm", new_callable=AsyncMock)
    async def test_successful_extraction(self, mock_llm):
        """Valid LLM response should parse into memory list."""
        mock_llm.return_value = '[{"content":"Python uses GIL","memory_type":"fact","summary":"Python GIL"}]'

        result = await extract_memories("x" * 200, "conv-123")
        assert len(result) == 1
        # Legacy "fact" type is migrated to "empirical" at extraction time
        assert result[0]["memory_type"] == "empirical"
        assert "GIL" in result[0]["content"]

    @pytest.mark.asyncio
    @patch("core.agents.memory.call_internal_llm", new_callable=AsyncMock)
    async def test_invalid_memory_type_defaults_to_fact(self, mock_llm):
        """Unknown memory_type should default to 'empirical'."""
        mock_llm.return_value = '[{"content":"test","memory_type":"invalid_type","summary":"test"}]'

        result = await extract_memories("x" * 200, "conv-123")
        assert result[0]["memory_type"] == "empirical"


# ---------------------------------------------------------------------------
# Budget-plumbing contract (Workstream A Phase 1.2)
# ---------------------------------------------------------------------------
#
# These tests gate the per-stage ``asyncio.wait_for`` budgets that bound
# /sdk/v1/memory/extract under its 10s SLO. They run inside the default
# ``test`` job — so they catch a "someone removed the wait_for" regression
# on every PR, complementing the live ``benchmark-slo`` job that catches
# real-OpenRouter regressions on the nightly schedule.
#
# Strategy: monkey-patch the module budget constant down to a fast value
# (50 ms), make the mocked LLM call take 200 ms, and assert the wait_for
# fires before the call completes. ~250 ms total runtime per test.


class TestExtractMemoriesBudget:
    @pytest.mark.asyncio
    async def test_extract_memories_returns_empty_on_timeout(self, monkeypatch):
        """If the extract LLM call exceeds MEMORY_LLM_BUDGET_S, the
        endpoint must return [] via the timeout-fallback branch — not
        propagate a TimeoutError."""
        import asyncio as _asyncio

        async def _slow_llm(*args, **kwargs):
            await _asyncio.sleep(0.2)
            return "[]"

        monkeypatch.setattr("core.agents.memory.MEMORY_LLM_BUDGET_S", 0.05)
        monkeypatch.setattr(
            "core.agents.memory.call_internal_llm", _slow_llm
        )
        result = await extract_memories("x" * 200, "conv-budget-test")
        assert result == []

    @pytest.mark.asyncio
    async def test_extract_memories_completes_under_budget(self, monkeypatch):
        """Sanity: a fast mocked LLM call inside the budget still succeeds —
        proves the wait_for wrapper passes the result through unchanged."""
        import asyncio as _asyncio

        async def _fast_llm(*args, **kwargs):
            await _asyncio.sleep(0.01)
            return '[{"content":"fast","memory_type":"fact","summary":"fast"}]'

        monkeypatch.setattr("core.agents.memory.MEMORY_LLM_BUDGET_S", 1.0)
        monkeypatch.setattr(
            "core.agents.memory.call_internal_llm", _fast_llm
        )
        result = await extract_memories("x" * 200, "conv-fast")
        assert len(result) == 1
        assert result[0]["memory_type"] == "empirical"

    @pytest.mark.asyncio
    async def test_extract_memories_logs_swallowed_on_timeout(
        self, monkeypatch, caplog
    ):
        """A budget-driven timeout must surface via ``log_swallowed_error``
        so /health.swallowed_errors_last_hour reflects it. Without this
        log, the long tail goes invisible — exactly the regression that
        Phase 1.2 was lifting."""
        import asyncio as _asyncio
        import logging

        async def _slow_llm(*args, **kwargs):
            await _asyncio.sleep(0.2)
            return "[]"

        monkeypatch.setattr("core.agents.memory.MEMORY_LLM_BUDGET_S", 0.05)
        monkeypatch.setattr(
            "core.agents.memory.call_internal_llm", _slow_llm
        )
        with caplog.at_level(logging.WARNING):
            await extract_memories("x" * 200, "conv-log-test")
        assert any(
            "extract_memories_timeout" in rec.message
            for rec in caplog.records
        ), f"Expected swallowed log; got: {[r.message for r in caplog.records]}"


class TestExtractAndStoreMemories:
    """Test full extraction + storage pipeline."""

    @pytest.mark.asyncio
    async def test_disabled_returns_skipped(self, monkeypatch):
        """Should skip when ENABLE_MEMORY_EXTRACTION is False."""
        monkeypatch.setattr("core.agents.memory.config.ENABLE_MEMORY_EXTRACTION", False)
        result = await extract_and_store_memories("text", "conv-123")
        assert result["status"] == "skipped"

    @pytest.mark.asyncio
    @patch("core.agents.memory.extract_memories", new_callable=AsyncMock)
    async def test_successful_storage(self, mock_extract, monkeypatch, mock_redis, mock_neo4j):
        """Extracted memories should be ingested into conversations domain."""
        monkeypatch.setattr("core.agents.memory.config.ENABLE_MEMORY_EXTRACTION", True)
        mock_extract.return_value = [
            {"content": "Python uses GIL", "memory_type": "fact", "summary": "GIL info"},
        ]
        mock_ingest = MagicMock(return_value={"status": "success", "artifact_id": "art-123"})

        result = await extract_and_store_memories(
            "x" * 200, "conv-123", "claude",
            redis_client=mock_redis,
            neo4j_driver=mock_neo4j[0],
            ingest_fn=mock_ingest,
        )
        assert result["memories_extracted"] == 1
        assert result["memories_stored"] == 1
        mock_ingest.assert_called_once()


class TestArchiveOldMemories:
    """Test memory retention/archival."""

    @pytest.mark.asyncio
    async def test_archive_query(self, mock_neo4j):
        """Should run archival Cypher query and return count."""
        driver, session = mock_neo4j
        mock_result = MagicMock()
        mock_record = MagicMock()
        mock_record.__getitem__ = lambda self, key: 5  # 5 archived
        mock_result.single.return_value = mock_record
        session.run.return_value = mock_result

        result = await archive_old_memories(driver, retention_days=90)
        assert result["archived_count"] == 5
        assert result["retention_days"] == 90
        session.run.assert_called_once()


class TestBridgeObservationDate:
    """The app-layer bridge stamps today's date for the live ingestion path so
    relative-time facts get a resolvable ``event_date``. Production previously
    passed None at every live call site → memories landed date-blind."""

    @pytest.mark.asyncio
    @patch(
        "app.agents.memory._core_extract_and_store_memories",
        new_callable=AsyncMock,
    )
    async def test_defaults_observation_date_to_today(self, mock_core):
        """No observation_date → bridge defaults to today (live conversation)."""
        from core.utils.time import utcnow_iso

        mock_core.return_value = {"status": "ok"}
        await extract_and_store_memories(
            "x" * 200, "conv-1", ingest_fn=MagicMock(),
        )
        assert mock_core.call_args.kwargs["observation_date"] == utcnow_iso()[:10]

    @pytest.mark.asyncio
    @patch(
        "app.agents.memory._core_extract_and_store_memories",
        new_callable=AsyncMock,
    )
    async def test_explicit_observation_date_passes_through(self, mock_core):
        """An explicit (historical) date is preserved, not overwritten."""
        mock_core.return_value = {"status": "ok"}
        await extract_and_store_memories(
            "x" * 200, "conv-1", ingest_fn=MagicMock(),
            observation_date="2023-01-15",
        )
        assert mock_core.call_args.kwargs["observation_date"] == "2023-01-15"
