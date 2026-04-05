# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for smart ingestion features.

Parser-level tests (eml, epub, rtf, html stripping, rtf stripping)
live in test_parsers.py. This file covers higher-level features:
mbox parsing, enhanced CSV, parser registry, semantic dedup,
OCR plugin structure, and feature flag gating.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestMboxParser:
    """Test .mbox mailbox file parsing."""

    def test_parse_mbox_single_message(self, tmp_path):
        """Parse an mbox with a single message."""
        mbox_content = (
            "From alice@example.com Tue Feb 25 10:00:00 2026\r\n"
            "From: alice@example.com\r\n"
            "Subject: Test Message\r\n"
            "Date: Tue, 25 Feb 2026 10:00:00 -0500\r\n"
            "\r\n"
            "This is the body.\r\n"
            "\r\n"
        )

        mbox_path = tmp_path / "test.mbox"
        mbox_path.write_bytes(mbox_content.encode("utf-8"))

        from app.parsers import parse_mbox

        result = parse_mbox(str(mbox_path))

        assert result["file_type"] == "mbox"
        assert result["page_count"] == 1
        assert "Test Message" in result["text"]
        assert "This is the body" in result["text"]


class TestEnhancedCsvParser:
    """Test enhanced CSV parser with delimiter detection and schema.

    Requires pandas — tests skip if pandas is not installed (host dev env).
    These tests run fully inside Docker where pandas is available.
    """

    @pytest.fixture(autouse=True)
    def _check_pandas(self):
        """Skip CSV tests if pandas is not available."""
        try:
            import pandas
            pandas.read_csv  # Verify it's the real module, not a stub
        except (ImportError, AttributeError):
            pytest.skip("pandas not available on host")

    def test_parse_csv_with_schema(self, tmp_path):
        """CSV parser returns schema summary and column types."""
        csv_content = "name,age,salary\nAlice,30,50000.50\nBob,25,45000.00\n"
        csv_path = tmp_path / "test.csv"
        csv_path.write_text(csv_content)

        from app.parsers import parse_csv

        result = parse_csv(str(csv_path))

        assert result["file_type"] == "csv"
        assert result["row_count"] == 2
        columns = json.loads(result["columns"])
        assert "name" in columns
        assert "age" in columns
        assert "salary" in columns
        assert "schema" in result
        schema = json.loads(result["schema"])
        assert "name" in schema

    def test_parse_tsv(self, tmp_path):
        """TSV file parsed with tab delimiter."""
        tsv_content = "col1\tcol2\tcol3\nval1\tval2\tval3\n"
        tsv_path = tmp_path / "test.tsv"
        tsv_path.write_text(tsv_content)

        from app.parsers import parse_csv

        result = parse_csv(str(tsv_path))

        assert result["file_type"] == "tsv"
        columns = json.loads(result["columns"])
        assert "col1" in columns

    def test_parse_semicolon_csv(self, tmp_path):
        """CSV with semicolon delimiter auto-detected."""
        csv_content = "name;value;count\nfoo;bar;10\nbaz;qux;20\n"
        csv_path = tmp_path / "semicolon.csv"
        csv_path.write_text(csv_content)

        from app.parsers import parse_csv

        result = parse_csv(str(csv_path))

        assert result["row_count"] == 2
        assert "Schema:" in result["text"]
        assert "Sample" in result["text"]

    def test_csv_truncation_warning(self, tmp_path):
        """Large CSV sets truncated flag."""
        lines = ["id,value"]
        for i in range(6000):
            lines.append(f"{i},{i*10}")
        csv_path = tmp_path / "large.csv"
        csv_path.write_text("\n".join(lines))

        from app.parsers import parse_csv

        result = parse_csv(str(csv_path))

        assert result["row_count"] == 6000
        assert result.get("truncated") is True


class TestParserRegistry:
    """Test that parser extensions are registered."""

    def test_new_extensions_in_registry(self):
        from app.parsers import PARSER_REGISTRY

        new_exts = [".eml", ".mbox", ".epub", ".rtf", ".tsv"]
        for ext in new_exts:
            assert ext in PARSER_REGISTRY, f"{ext} not registered in PARSER_REGISTRY"

    def test_new_extensions_in_config(self):
        import config

        new_exts = {".eml", ".mbox", ".epub", ".rtf", ".tsv"}
        for ext in new_exts:
            assert ext in config.SUPPORTED_EXTENSIONS, f"{ext} not in SUPPORTED_EXTENSIONS"

    def test_parse_file_dispatches_eml(self, tmp_path):
        """parse_file correctly dispatches .eml to parse_eml."""
        eml = (
            "From: test@test.com\r\n"
            "Subject: Dispatch Test\r\n"
            "Content-Type: text/plain\r\n"
            "\r\n"
            "Body text"
        )
        eml_path = tmp_path / "dispatch.eml"
        eml_path.write_bytes(eml.encode())

        from app.parsers import parse_file

        result = parse_file(str(eml_path))
        assert result["file_type"] == "eml"
        assert "Dispatch Test" in result["text"]


class TestSemanticDedup:
    """Test semantic deduplication utility."""

    def test_no_dup_when_collection_empty(self):
        from utils.dedup import check_semantic_duplicate

        mock_collection = MagicMock()
        mock_collection.count.return_value = 0

        mock_chroma = MagicMock()
        mock_chroma.get_or_create_collection.return_value = mock_collection

        result = check_semantic_duplicate(
            text="Some document text",
            domain="coding",
            chroma_client=mock_chroma,
        )
        assert result is None

    def test_no_dup_when_distance_high(self):
        from utils.dedup import check_semantic_duplicate

        mock_collection = MagicMock()
        mock_collection.count.return_value = 5
        mock_collection.query.return_value = {
            "ids": [["chunk_1"]],
            "distances": [[10.0]],
            "metadatas": [[{"artifact_id": "abc", "filename": "old.py"}]],
        }

        mock_chroma = MagicMock()
        mock_chroma.get_or_create_collection.return_value = mock_collection

        result = check_semantic_duplicate(
            text="Some document text",
            domain="coding",
            chroma_client=mock_chroma,
        )
        assert result is None

    def test_dup_detected_when_distance_low(self):
        from utils.dedup import check_semantic_duplicate

        mock_collection = MagicMock()
        mock_collection.count.return_value = 5
        mock_collection.query.return_value = {
            "ids": [["chunk_1"]],
            "distances": [[0.01]],
            "metadatas": [[{"artifact_id": "abc-123", "filename": "original.py"}]],
        }

        mock_chroma = MagicMock()
        mock_chroma.get_or_create_collection.return_value = mock_collection

        result = check_semantic_duplicate(
            text="Some document text",
            domain="coding",
            chroma_client=mock_chroma,
        )
        assert result is not None
        assert result["artifact_id"] == "abc-123"
        assert result["filename"] == "original.py"
        assert result["similarity"] > 0.9

    def test_skip_self_match(self):
        from utils.dedup import check_semantic_duplicate

        mock_collection = MagicMock()
        mock_collection.count.return_value = 5
        mock_collection.query.return_value = {
            "ids": [["chunk_1"]],
            "distances": [[0.01]],
            "metadatas": [[{"artifact_id": "self-id", "filename": "same.py"}]],
        }

        mock_chroma = MagicMock()
        mock_chroma.get_or_create_collection.return_value = mock_collection

        result = check_semantic_duplicate(
            text="Some document text",
            domain="coding",
            chroma_client=mock_chroma,
            exclude_artifact_id="self-id",
        )
        assert result is None

    def test_empty_text_returns_none(self):
        from utils.dedup import check_semantic_duplicate

        result = check_semantic_duplicate(
            text="",
            domain="coding",
            chroma_client=MagicMock(),
        )
        assert result is None


class TestOCRPluginManifest:
    """Test OCR plugin manifest and structure."""

    def test_manifest_exists(self):
        manifest_path = Path(__file__).parent.parent / "plugins" / "ocr" / "manifest.json"
        assert manifest_path.exists(), "OCR plugin manifest.json not found"

        manifest = json.loads(manifest_path.read_text())
        assert manifest["name"] == "ocr"
        assert manifest["type"] == "parser"
        assert manifest["tier"] == "pro"
        assert "version" in manifest

    def test_plugin_module_exists(self):
        plugin_path = Path(__file__).parent.parent / "plugins" / "ocr" / "plugin.py"
        assert plugin_path.exists(), "OCR plugin plugin.py not found"

        content = plugin_path.read_text()
        assert "def register():" in content
        assert "parse_pdf_with_ocr" in content

    def test_plugin_not_loaded_in_community_tier(self):
        from plugins import _load_single_plugin

        plugin_dir = Path(__file__).parent.parent / "plugins" / "ocr"

        with patch("config.FEATURE_TIER", "community"):
            result = _load_single_plugin(plugin_dir)
            assert result is None


class TestFeatureFlagIntegration:
    """Test that Pro features are properly gated."""

    def test_semantic_dedup_enabled_in_community(self):
        import config

        if config.FEATURE_TIER == "community":
            # semantic_dedup is a community feature — always enabled
            assert config.FEATURE_FLAGS["semantic_dedup"] is True

    def test_ocr_enabled_in_community(self):
        import config

        if config.FEATURE_TIER == "community":
            # ocr_parsing is a community feature — always enabled
            assert config.FEATURE_FLAGS["ocr_parsing"] is True
