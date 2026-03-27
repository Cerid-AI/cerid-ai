# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for tools.py — MCP tool registry and execute_tool() dispatcher."""

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tools import MCP_TOOLS, execute_tool

# ---------------------------------------------------------------------------
# Tests: Tool registry structure
# ---------------------------------------------------------------------------

class TestToolRegistry:
    def test_has_tools(self):
        assert len(MCP_TOOLS) > 0

    def test_all_have_name(self):
        for tool in MCP_TOOLS:
            assert "name" in tool
            assert isinstance(tool["name"], str)

    def test_all_have_description(self):
        for tool in MCP_TOOLS:
            assert "description" in tool
            assert len(tool["description"]) > 0

    def test_all_have_input_schema(self):
        for tool in MCP_TOOLS:
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"

    def test_unique_names(self):
        names = [t["name"] for t in MCP_TOOLS]
        assert len(names) == len(set(names))

    def test_expected_tools_present(self):
        names = {t["name"] for t in MCP_TOOLS}
        expected = {
            "pkb_query", "pkb_ingest", "pkb_ingest_file", "pkb_health",
            "pkb_collections", "pkb_agent_query", "pkb_artifacts",
            "pkb_recategorize", "pkb_triage", "pkb_rectify", "pkb_audit",
            "pkb_maintain",
        }
        assert expected.issubset(names)

    def test_schemas_have_properties(self):
        for tool in MCP_TOOLS:
            assert "properties" in tool["inputSchema"]

    def test_required_fields_defined(self):
        """Tools with required params should have 'required' key."""
        for tool in MCP_TOOLS:
            schema = tool["inputSchema"]
            if "required" in schema:
                assert isinstance(schema["required"], list)
                for req in schema["required"]:
                    assert req in schema["properties"]


# ---------------------------------------------------------------------------
# Tests: execute_tool — unknown tool
# ---------------------------------------------------------------------------

class TestExecuteToolUnknown:
    def test_unknown_tool_raises(self):
        with pytest.raises(ValueError, match="Unknown tool"):
            asyncio.get_event_loop().run_until_complete(
                execute_tool("nonexistent_tool", {})
            )


# ---------------------------------------------------------------------------
# Tests: execute_tool — sync tools (non-async handlers)
# ---------------------------------------------------------------------------

class TestExecuteToolSync:
    @patch("app.tools.query_knowledge")
    def test_pkb_query(self, mock_qk):
        mock_qk.return_value = {"results": []}
        asyncio.get_event_loop().run_until_complete(
            execute_tool("pkb_query", {"query": "test"})
        )
        mock_qk.assert_called_once_with(query="test")

    @patch("app.tools.ingest_content")
    def test_pkb_ingest(self, mock_ic):
        mock_ic.return_value = {"status": "success"}
        asyncio.get_event_loop().run_until_complete(
            execute_tool("pkb_ingest", {"content": "hello", "domain": "coding"})
        )
        mock_ic.assert_called_once_with("hello", "coding")

    @patch("app.tools.ingest_content")
    def test_pkb_ingest_defaults(self, mock_ic):
        mock_ic.return_value = {"status": "success"}
        asyncio.get_event_loop().run_until_complete(
            execute_tool("pkb_ingest", {})
        )
        mock_ic.assert_called_once_with("", "general")

    @patch("app.tools.health_check")
    def test_pkb_health(self, mock_hc):
        mock_hc.return_value = {"status": "healthy"}
        result = asyncio.get_event_loop().run_until_complete(
            execute_tool("pkb_health", {})
        )
        assert result["status"] == "healthy"

    @patch("app.tools.list_collections")
    def test_pkb_collections(self, mock_lc):
        mock_lc.return_value = {"collections": []}
        asyncio.get_event_loop().run_until_complete(
            execute_tool("pkb_collections", {})
        )
        mock_lc.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: execute_tool — artifacts
# ---------------------------------------------------------------------------

class TestExecuteToolArtifacts:
    @patch("app.tools.get_neo4j")
    @patch("app.tools.graph")
    def test_pkb_artifacts_with_domain(self, mock_graph, mock_get_neo4j):
        mock_driver = MagicMock()
        mock_get_neo4j.return_value = mock_driver
        mock_graph.list_artifacts.return_value = []

        asyncio.get_event_loop().run_until_complete(
            execute_tool("pkb_artifacts", {"domain": "coding", "limit": 10})
        )
        mock_graph.list_artifacts.assert_called_once_with(
            mock_driver, domain="coding", limit=10
        )

    @patch("app.tools.get_neo4j")
    @patch("app.tools.graph")
    def test_pkb_artifacts_empty_domain_becomes_none(self, mock_graph, mock_get_neo4j):
        mock_get_neo4j.return_value = MagicMock()
        mock_graph.list_artifacts.return_value = []

        asyncio.get_event_loop().run_until_complete(
            execute_tool("pkb_artifacts", {"domain": ""})
        )
        # Empty string should convert to None
        call_args = mock_graph.list_artifacts.call_args
        assert call_args.kwargs["domain"] is None


# ---------------------------------------------------------------------------
# Tests: execute_tool — recategorize
# ---------------------------------------------------------------------------

class TestExecuteToolRecategorize:
    @patch("app.tools.recategorize")
    def test_pkb_recategorize(self, mock_recat):
        mock_recat.return_value = {"status": "success"}
        asyncio.get_event_loop().run_until_complete(
            execute_tool("pkb_recategorize", {
                "artifact_id": "a1",
                "new_domain": "finance",
                "tags": "important",
            })
        )
        mock_recat.assert_called_once_with(
            artifact_id="a1", new_domain="finance", tags="important"
        )

    @patch("app.tools.recategorize")
    def test_pkb_recategorize_missing_required_raises(self, mock_recat):
        with pytest.raises(KeyError):
            asyncio.get_event_loop().run_until_complete(
                execute_tool("pkb_recategorize", {})
            )


# ---------------------------------------------------------------------------
# Tests: execute_tool — triage (complex flow)
# ---------------------------------------------------------------------------

class TestExecuteToolTriage:
    @patch("app.tools.ingest_content")
    def test_triage_error_returns_early(self, mock_ic):
        triage_result = {"status": "error", "error": "File not found"}

        with patch("agents.triage.triage_file", new_callable=AsyncMock) as mock_tf:
            mock_tf.return_value = triage_result
            result = asyncio.get_event_loop().run_until_complete(
                execute_tool("pkb_triage", {"file_path": os.path.join(tempfile.gettempdir(), "nope.txt")})
            )

        assert result["status"] == "error"
        assert result["error"] == "File not found"
        mock_ic.assert_not_called()  # Should NOT proceed to ingest

    @patch("app.tools.ingest_content")
    def test_triage_success_ingests(self, mock_ic):
        triage_result = {
            "status": "parsed",
            "parsed_text": "file content",
            "domain": "coding",
            "metadata": {"keywords": "test"},
            "filename": "test.py",
            "categorize_mode": "smart",
        }
        mock_ic.return_value = {"status": "success", "artifact_id": "a1"}

        with patch("agents.triage.triage_file", new_callable=AsyncMock) as mock_tf:
            mock_tf.return_value = triage_result
            result = asyncio.get_event_loop().run_until_complete(
                execute_tool("pkb_triage", {"file_path": os.path.join(tempfile.gettempdir(), "test.py")})
            )

        assert result["filename"] == "test.py"
        assert result["triage_status"] == "parsed"
        mock_ic.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: execute_tool — async agent tools
# ---------------------------------------------------------------------------

class TestExecuteToolAgents:
    @patch("app.tools.get_redis")
    @patch("app.tools.get_chroma")
    @patch("app.tools.get_neo4j")
    def test_pkb_agent_query(self, mock_neo4j, mock_chroma, mock_redis):
        mock_neo4j.return_value = MagicMock()
        mock_chroma.return_value = MagicMock()
        mock_redis.return_value = MagicMock()

        with patch("agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq:
            mock_aq.return_value = {"context": "result", "sources": []}
            result = asyncio.get_event_loop().run_until_complete(
                execute_tool("pkb_agent_query", {"query": "test"})
            )
        assert result["context"] == "result"

    @patch("app.tools.get_redis")
    def test_pkb_audit(self, mock_redis):
        mock_redis.return_value = MagicMock()

        with patch("agents.audit.audit", new_callable=AsyncMock) as mock_audit:
            mock_audit.return_value = {"timestamp": "2026-01-01"}
            result = asyncio.get_event_loop().run_until_complete(
                execute_tool("pkb_audit", {"reports": ["activity"], "hours": 12})
            )
        mock_audit.assert_called_once()
        assert result["timestamp"] == "2026-01-01"

    @patch("app.tools.get_redis")
    @patch("app.tools.get_chroma")
    @patch("app.tools.get_neo4j")
    def test_pkb_rectify(self, mock_neo4j, mock_chroma, mock_redis):
        mock_neo4j.return_value = MagicMock()
        mock_chroma.return_value = MagicMock()
        mock_redis.return_value = MagicMock()

        with patch("agents.rectify.rectify", new_callable=AsyncMock) as mock_rect:
            mock_rect.return_value = {"findings": {}}
            asyncio.get_event_loop().run_until_complete(
                execute_tool("pkb_rectify", {"auto_fix": True, "stale_days": 30})
            )
        call_kwargs = mock_rect.call_args.kwargs
        assert call_kwargs["auto_fix"] is True
        assert call_kwargs["stale_days"] == 30

    @patch("app.tools.get_redis")
    @patch("app.tools.get_chroma")
    @patch("app.tools.get_neo4j")
    def test_pkb_maintain(self, mock_neo4j, mock_chroma, mock_redis):
        mock_neo4j.return_value = MagicMock()
        mock_chroma.return_value = MagicMock()
        mock_redis.return_value = MagicMock()

        with patch("agents.maintenance.maintain", new_callable=AsyncMock) as mock_maint:
            mock_maint.return_value = {"actions_run": ["health"]}
            asyncio.get_event_loop().run_until_complete(
                execute_tool("pkb_maintain", {"actions": ["health"]})
            )
        mock_maint.assert_called_once()
