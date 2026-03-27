# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for agents/triage.py — LangGraph ingestion routing nodes and logic.

Tests focus on individual node functions and routing decisions rather than
the full compiled graph (which requires langgraph runtime).
"""

import os
import tempfile
from unittest.mock import patch

from agents.triage import (
    chunk_node,
    extract_metadata_node,
    parse_node,
    route_categorization,
    should_categorize,
    should_continue_after_parse,
    should_continue_after_validate,
    validate_node,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_state(**overrides) -> dict:
    """Create a base triage state dict with sensible defaults."""
    state = {
        "file_path": "",
        "filename": "",
        "domain": "",
        "categorize_mode": "",
        "tags": "",
        "parsed_text": "",
        "file_type": "",
        "page_count": None,
        "metadata": {},
        "chunks": [],
        "content_hash": "",
        "artifact_id": "",
        "needs_ai_categorization": False,
        "is_structured": False,
        "status": "pending",
        "error": "",
        "result": {},
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Tests: validate_node
# ---------------------------------------------------------------------------

class TestValidateNode:
    def test_file_not_found(self, tmp_path):
        state = _base_state(file_path=str(tmp_path / "nonexistent.py"))
        result = validate_node(state)
        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.py"
        f.touch()
        state = _base_state(file_path=str(f))
        result = validate_node(state)
        assert result["status"] == "error"
        assert "empty" in result["error"]

    def test_unsupported_extension(self, tmp_path):
        f = tmp_path / "test.xyz"
        f.write_text("content")
        state = _base_state(file_path=str(f))
        result = validate_node(state)
        assert result["status"] == "error"
        assert "Unsupported" in result["error"]

    def test_valid_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        state = _base_state(file_path=str(f))
        result = validate_node(state)
        assert result["status"] != "error"
        assert result["filename"] == "test.txt"
        assert result["file_type"] == "txt"

    def test_sets_filename_and_type(self, tmp_path):
        f = tmp_path / "readme.md"
        f.write_text("# Title")
        state = _base_state(file_path=str(f))
        result = validate_node(state)
        assert result["filename"] == "readme.md"
        assert result["file_type"] == "md"


# ---------------------------------------------------------------------------
# Tests: parse_node
# ---------------------------------------------------------------------------

class TestParseNode:
    @patch("app.agents.triage.parse_file")
    def test_successful_parse(self, mock_parse):
        mock_parse.return_value = {
            "text": "parsed content here",
            "file_type": "txt",
        }
        state = _base_state(file_path=os.path.join(tempfile.gettempdir(), "test.txt"))
        result = parse_node(state)
        assert result["status"] == "parsed"
        assert result["parsed_text"] == "parsed content here"

    @patch("app.agents.triage.parse_file")
    def test_parse_error(self, mock_parse):
        mock_parse.side_effect = ValueError("Bad file format")
        state = _base_state(file_path=os.path.join(tempfile.gettempdir(), "test.txt"))
        result = parse_node(state)
        assert result["status"] == "error"
        assert "Bad file format" in result["error"]

    @patch("app.agents.triage.parse_file")
    def test_structured_data_detected(self, mock_parse):
        mock_parse.return_value = {
            "text": "table data",
            "file_type": "pdf",
            "table_count": 3,
        }
        state = _base_state(file_path=os.path.join(tempfile.gettempdir(), "test.pdf"))
        result = parse_node(state)
        assert result["is_structured"] is True

    @patch("app.agents.triage.parse_file")
    def test_xlsx_is_structured(self, mock_parse):
        mock_parse.return_value = {
            "text": "spreadsheet data",
            "file_type": "xlsx",
        }
        state = _base_state(file_path=os.path.join(tempfile.gettempdir(), "test.xlsx"), file_type="xlsx")
        result = parse_node(state)
        assert result["is_structured"] is True

    @patch("app.agents.triage.parse_file")
    def test_unexpected_error_caught(self, mock_parse):
        mock_parse.side_effect = RuntimeError("unexpected")
        state = _base_state(file_path=os.path.join(tempfile.gettempdir(), "test.txt"))
        result = parse_node(state)
        assert result["status"] == "error"
        assert "Parse failed" in result["error"]


# ---------------------------------------------------------------------------
# Tests: route_categorization
# ---------------------------------------------------------------------------

class TestRouteCategorization:
    def test_explicit_valid_domain_skips_ai(self):
        from config import DOMAINS
        state = _base_state(domain=DOMAINS[0])
        result = route_categorization(state)
        assert result["needs_ai_categorization"] is False
        assert result["categorize_mode"] == "manual"

    def test_manual_mode_uses_default_domain(self):
        from config import DEFAULT_DOMAIN
        state = _base_state(categorize_mode="manual")
        result = route_categorization(state)
        assert result["needs_ai_categorization"] is False
        assert result["domain"] == DEFAULT_DOMAIN

    def test_empty_domain_triggers_ai(self):
        state = _base_state(domain="", categorize_mode="smart")
        result = route_categorization(state)
        assert result["needs_ai_categorization"] is True
        assert result["categorize_mode"] == "smart"

    def test_invalid_domain_triggers_ai(self):
        state = _base_state(domain="nonexistent_domain_xyz", categorize_mode="pro")
        result = route_categorization(state)
        assert result["needs_ai_categorization"] is True


# ---------------------------------------------------------------------------
# Tests: extract_metadata_node
# ---------------------------------------------------------------------------

class TestExtractMetadataNode:
    @patch("app.agents.triage.extract_metadata")
    def test_merges_metadata(self, mock_extract):
        mock_extract.return_value = {"word_count": 100, "summary": "local summary"}
        state = _base_state(
            parsed_text="test content",
            filename="test.py",
            domain="coding",
            metadata={"summary": "AI summary", "keywords": '["python"]'},
        )
        result = extract_metadata_node(state)
        # AI metadata overrides local (existing_meta takes precedence in merge)
        assert result["metadata"]["summary"] == "AI summary"
        assert result["metadata"]["file_type"] == ""

    @patch("app.agents.triage.extract_metadata")
    def test_adds_file_type(self, mock_extract):
        mock_extract.return_value = {}
        state = _base_state(file_type="pdf", page_count=5)
        result = extract_metadata_node(state)
        assert result["metadata"]["file_type"] == "pdf"
        assert result["metadata"]["page_count"] == 5

    @patch("app.agents.triage.extract_metadata")
    def test_ai_categorized_flag(self, mock_extract):
        mock_extract.return_value = {}
        state = _base_state(needs_ai_categorization=True)
        result = extract_metadata_node(state)
        assert result["metadata"]["ai_categorized"] == "true"

    @patch("app.agents.triage.extract_metadata")
    def test_tags_preserved(self, mock_extract):
        mock_extract.return_value = {}
        state = _base_state(tags="important,review")
        result = extract_metadata_node(state)
        assert result["metadata"]["tags"] == "important,review"


# ---------------------------------------------------------------------------
# Tests: chunk_node
# ---------------------------------------------------------------------------

class TestChunkNode:
    @patch("app.agents.triage.chunk_text")
    def test_produces_chunks(self, mock_chunk):
        mock_chunk.return_value = ["chunk 1", "chunk 2"]
        state = _base_state(parsed_text="some text to chunk")
        result = chunk_node(state)
        assert result["chunks"] == ["chunk 1", "chunk 2"]
        mock_chunk.assert_called_once()

    @patch("app.agents.triage.chunk_text")
    def test_empty_text(self, mock_chunk):
        mock_chunk.return_value = []
        state = _base_state(parsed_text="")
        result = chunk_node(state)
        assert result["chunks"] == []


# ---------------------------------------------------------------------------
# Tests: Routing functions (pure)
# ---------------------------------------------------------------------------

class TestRoutingFunctions:
    def test_validate_error_routes_to_error_end(self):
        assert should_continue_after_validate({"status": "error"}) == "error_end"

    def test_validate_ok_routes_to_parse(self):
        assert should_continue_after_validate({"status": "pending"}) == "parse"

    def test_parse_error_routes_to_error_end(self):
        assert should_continue_after_parse({"status": "error"}) == "error_end"

    def test_parse_ok_routes_to_categorization(self):
        assert should_continue_after_parse({"status": "parsed"}) == "route_categorization"

    def test_needs_ai_routes_to_categorize(self):
        assert should_categorize({"needs_ai_categorization": True}) == "categorize"

    def test_no_ai_routes_to_metadata(self):
        assert should_categorize({"needs_ai_categorization": False}) == "extract_metadata"
