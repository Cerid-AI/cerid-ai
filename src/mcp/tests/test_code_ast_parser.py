# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the tree-sitter code AST parser + chunker (Phase 2b.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.ingest.chunkers import chunk_elements
from core.ingest.chunkers.code_strategy import code_chunk_strategy
from core.ingest.parsers.code_ast import _supported_extensions, parse_code


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


SAMPLE_PY = '''import os
from pathlib import Path

CONST = 42


def add(a: int, b: int) -> int:
    """Return a plus b."""
    return a + b


class Calculator:
    """A simple calculator."""

    def __init__(self, base: int = 0):
        self.base = base

    def add(self, x: int) -> int:
        return self.base + x


def subtract(a: int, b: int) -> int:
    return a - b
'''


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_code_extracts_top_level_definitions(tmp_path):
    p = _write(tmp_path, "calc.py", SAMPLE_PY)
    elements = parse_code(p)
    types = [el["element_type"] for el in elements]
    # Two imports + add + Calculator + subtract
    assert types.count("CodeImport") == 2
    assert types.count("CodeFunction") == 2
    assert types.count("CodeClass") == 1


def test_parse_code_extracts_function_names(tmp_path):
    p = _write(tmp_path, "calc.py", SAMPLE_PY)
    elements = parse_code(p)
    func_names = [
        el["metadata"]["name"]
        for el in elements
        if el["element_type"] == "CodeFunction"
    ]
    assert sorted(func_names) == ["add", "subtract"]


def test_parse_code_extracts_class_name_and_methods_stay_inside(tmp_path):
    """Class methods are NOT emitted as top-level CodeFunction —
    they live inside the CodeClass element so the class body stays
    coherent."""
    p = _write(tmp_path, "calc.py", SAMPLE_PY)
    elements = parse_code(p)
    classes = [el for el in elements if el["element_type"] == "CodeClass"]
    assert len(classes) == 1
    assert classes[0]["metadata"]["name"] == "Calculator"
    # The class element's text contains both __init__ and add methods
    assert "__init__" in classes[0]["text"]
    assert "self.base + x" in classes[0]["text"]


def test_parse_code_records_line_ranges(tmp_path):
    p = _write(tmp_path, "calc.py", SAMPLE_PY)
    elements = parse_code(p)
    # The first import is on line 1
    first = next(el for el in elements if el["element_type"] == "CodeImport")
    assert first["metadata"]["start_line"] == 1
    # All elements have valid line ranges (start <= end)
    for el in elements:
        meta = el["metadata"]
        assert meta["start_line"] <= meta["end_line"]


def test_parse_code_includes_file_and_language(tmp_path):
    p = _write(tmp_path, "calc.py", SAMPLE_PY)
    elements = parse_code(p)
    for el in elements:
        assert el["metadata"]["language"] == "python"
        assert el["metadata"]["file"].endswith("calc.py")


def test_parse_code_unsupported_extension_returns_empty(tmp_path):
    """Languages not yet wired (.go, .rs, .ts) return empty so the
    upstream registry shim falls back to the legacy token chunker."""
    p = _write(tmp_path, "main.go", "package main\nfunc main() {}\n")
    assert parse_code(p) == []


def test_parse_code_empty_file_returns_empty(tmp_path):
    p = _write(tmp_path, "empty.py", "")
    assert parse_code(p) == []


def test_parse_code_raises_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_code(tmp_path / "does_not_exist.py")


def test_supported_extensions_includes_python():
    assert ".py" in _supported_extensions()


# ---------------------------------------------------------------------------
# Chunker strategy
# ---------------------------------------------------------------------------


def test_code_strategy_prepends_file_and_name_breadcrumb():
    element = {
        "text": "def add(a, b):\n    return a + b",
        "element_type": "CodeFunction",
        "metadata": {
            "file": "src/mcp/foo.py",
            "language": "python",
            "name": "add",
            "qualified_name": "add",
            "start_line": 5,
            "end_line": 6,
        },
    }
    chunks = code_chunk_strategy(element)
    assert len(chunks) == 1
    text = chunks[0]["text"]
    assert text.startswith("# src/mcp/foo.py :: add")
    assert "return a + b" in text


def test_code_strategy_import_breadcrumb_omits_name():
    element = {
        "text": "import os",
        "element_type": "CodeImport",
        "metadata": {
            "file": "src/mcp/foo.py",
            "language": "python",
            "start_line": 1,
            "end_line": 1,
        },
    }
    chunks = code_chunk_strategy(element)
    text = chunks[0]["text"]
    # No '::' segment when there's no name
    assert "::" not in text
    assert text.startswith("# src/mcp/foo.py")
    assert "import os" in text


def test_code_strategy_metadata_propagates():
    element = {
        "text": "def foo(): pass",
        "element_type": "CodeFunction",
        "metadata": {
            "file": "x.py", "language": "python", "name": "foo",
            "qualified_name": "foo", "start_line": 1, "end_line": 1,
        },
    }
    chunks = code_chunk_strategy(element)
    md = chunks[0]["metadata"]
    assert md["element_type"] == "CodeFunction"
    assert md["name"] == "foo"
    assert md["file"] == "x.py"
    assert md["language"] == "python"


def test_code_strategy_splits_oversized_body(monkeypatch):
    import config

    monkeypatch.setattr(config, "PARENT_CHUNK_TOKENS", 30)
    long_body = "def big():\n" + ("    x = 1\n" * 60)
    element = {
        "text": long_body,
        "element_type": "CodeFunction",
        "metadata": {
            "file": "big.py", "language": "python", "name": "big",
            "qualified_name": "big", "start_line": 1, "end_line": 60,
        },
    }
    chunks = code_chunk_strategy(element)
    assert len(chunks) >= 2
    for c in chunks:
        # Breadcrumb sticks to every sub-chunk
        assert c["text"].startswith("# big.py :: big")
    indices = [c["metadata"].get("code_chunk_idx") for c in chunks]
    assert indices == list(range(len(chunks)))


# ---------------------------------------------------------------------------
# End-to-end dispatch
# ---------------------------------------------------------------------------


def test_chunk_elements_dispatches_code_strategies(tmp_path):
    p = _write(tmp_path, "calc.py", SAMPLE_PY)
    elements = parse_code(p)
    chunks = chunk_elements(elements)
    # Each top-level def becomes one chunk; imports each become one chunk.
    # Assert breadcrumbs landed and element_types preserved.
    types = sorted({c["metadata"]["element_type"] for c in chunks})
    assert "CodeFunction" in types
    assert "CodeClass" in types
    assert "CodeImport" in types
    for c in chunks:
        assert c["text"].startswith("# ")
