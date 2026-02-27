# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for graph utilities (Phase 4B.2)."""


from utils.graph import _extract_references, _parse_keywords


class TestParseKeywords:
    def test_valid_json(self):
        result = _parse_keywords('["Python", "FastAPI", "REST"]')
        assert result == {"python", "fastapi", "rest"}

    def test_empty(self):
        assert _parse_keywords("") == set()
        assert _parse_keywords("[]") == set()
        assert _parse_keywords(None) == set()

    def test_invalid_json(self):
        assert _parse_keywords("not json") == set()

    def test_strips_whitespace(self):
        result = _parse_keywords('["  hello  ", "world"]')
        assert result == {"hello", "world"}


class TestExtractReferences:
    def test_python_imports(self):
        content = "import os\nfrom pathlib import Path\nfrom utils.graph import create_artifact"
        refs = _extract_references(content, "main.py")
        assert "os.py" in refs
        assert "graph.py" in refs

    def test_js_imports(self):
        content = "import { useState } from 'react'\nimport App from './App'"
        refs = _extract_references(content, "index.tsx")
        assert "App" in refs or "App.js" in refs or "App.tsx" in refs

    def test_file_mentions(self):
        content = "See the configuration in config.py for details"
        refs = _extract_references(content, "readme.md")
        assert "config.py" in refs

    def test_no_self_reference(self):
        content = "This is utils.py with import statements"
        refs = _extract_references(content, "utils.py")
        assert "utils.py" not in refs

    def test_no_prose_import(self):
        # Should not match "import" in prose (not at line start)
        content = "The data was imported from Excel spreadsheets"
        refs = _extract_references(content, "report.md")
        # Should not create references from prose
        assert "Excel.py" not in refs