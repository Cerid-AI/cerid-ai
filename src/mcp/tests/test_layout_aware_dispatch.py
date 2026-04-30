# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the layout-aware ingest dispatcher (Phase 2b wire-in)."""

from __future__ import annotations

import json
from pathlib import Path

from core.ingest.dispatch import (
    is_supported,
    layout_aware_parse,
    supported_extensions,
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Extension support
# ---------------------------------------------------------------------------


def test_is_supported_lower_cases_extension():
    assert is_supported(".csv") is True
    assert is_supported(".CSV") is True
    assert is_supported(".MD") is True


def test_is_supported_lists_phase_2b_extensions():
    exts = supported_extensions()
    for required in (".csv", ".md", ".markdown", ".py"):
        assert required in exts


def test_is_supported_rejects_unwired_extensions():
    # Phase 2b HTML hasn't landed yet — it must fall through to the
    # legacy parser. Email/PDF/XLSX/DOCX wired in 2b.4–2b.7.
    assert is_supported(".html") is False
    assert is_supported(".htm") is False


def test_is_supported_includes_full_phase_2b_set():
    """Phase 2b end-state: email, PDF, XLSX, DOCX all wired."""
    for ext in (".eml", ".pdf", ".xlsx", ".docx"):
        assert is_supported(ext) is True
        assert ext in supported_extensions()


# ---------------------------------------------------------------------------
# layout_aware_parse — happy paths
# ---------------------------------------------------------------------------


def test_layout_aware_parse_csv_emits_one_chunk_per_row(tmp_path):
    p = _write(
        tmp_path, "sales.csv",
        "quarter,revenue\nQ1,1500000\nQ2,1800000\n",
    )
    result = layout_aware_parse(p)
    assert result is not None
    raw_text, chunks = result

    # Raw text matches the file contents (used by caller for content_hash)
    assert "quarter,revenue" in raw_text
    assert "Q1,1500000" in raw_text

    # Two data rows = two chunks
    assert len(chunks) == 2

    for chunk in chunks:
        assert chunk["metadata"]["element_type"] == "CSVRow"
        # column_headers stored as Python list — services/ingestion.py
        # JSON-coerces at the ChromaDB write boundary
        assert chunk["metadata"]["column_headers"] == ["quarter", "revenue"]


def test_layout_aware_parse_markdown_breadcrumb_in_chunks(tmp_path):
    p = _write(
        tmp_path, "doc.md",
        "# Top\n## Sub\nLeaf body text.\n",
    )
    result = layout_aware_parse(p)
    assert result is not None
    raw_text, chunks = result

    assert raw_text.startswith("# Top")
    assert len(chunks) == 1
    # Heading breadcrumb prepended at the chunker stage
    assert chunks[0]["text"].startswith("Top > Sub")
    assert chunks[0]["metadata"]["heading_path"] == ["Top", "Sub"]


def test_layout_aware_parse_python_extracts_top_level_definitions(tmp_path):
    p = _write(
        tmp_path, "calc.py",
        "import os\n\n"
        "def add(a, b):\n    return a + b\n\n"
        "class Calc:\n    def add(self, x): return x + 1\n",
    )
    result = layout_aware_parse(p)
    assert result is not None
    _, chunks = result

    types = sorted({c["metadata"]["element_type"] for c in chunks})
    assert "CodeImport" in types
    assert "CodeFunction" in types
    assert "CodeClass" in types

    # File-path breadcrumb sticks to every chunk
    for chunk in chunks:
        assert chunk["text"].startswith("# ") and "calc.py" in chunk["text"]


# ---------------------------------------------------------------------------
# layout_aware_parse — fall-through paths
# ---------------------------------------------------------------------------


def test_layout_aware_parse_unsupported_extension_returns_none(tmp_path):
    """PDFs / DOCX / HTML must return None so the caller falls back to
    the legacy parse_file path."""
    p = _write(tmp_path, "note.txt", "plain text content")
    assert layout_aware_parse(p) is None


def test_layout_aware_parse_missing_file_returns_none(tmp_path):
    """The dispatcher never raises on missing file — it returns None
    so the caller's legacy fallback can produce its own error."""
    assert layout_aware_parse(tmp_path / "nope.csv") is None


def test_layout_aware_parse_empty_csv_returns_none(tmp_path):
    """An empty CSV (no data rows) yields zero elements; the dispatcher
    returns None so the legacy parser at least tries flat-text extraction."""
    p = _write(tmp_path, "empty.csv", "header_only\n")
    assert layout_aware_parse(p) is None


def test_layout_aware_parse_handles_extension_case_insensitively(tmp_path):
    p = _write(tmp_path, "data.CSV", "col\nvalue\n")
    result = layout_aware_parse(p)
    assert result is not None


# ---------------------------------------------------------------------------
# ingest_content metadata coercion (the ChromaDB-compat helper)
# ---------------------------------------------------------------------------


def test_coerce_chroma_meta_passes_primitives_through():
    from app.services.ingestion import _coerce_chroma_meta

    assert _coerce_chroma_meta("string") == "string"
    assert _coerce_chroma_meta(42) == 42
    assert _coerce_chroma_meta(3.14) == 3.14
    assert _coerce_chroma_meta(True) is True
    assert _coerce_chroma_meta(None) is None


def test_coerce_chroma_meta_json_encodes_lists():
    from app.services.ingestion import _coerce_chroma_meta

    out = _coerce_chroma_meta(["quarter", "revenue", "region"])
    assert isinstance(out, str)
    assert json.loads(out) == ["quarter", "revenue", "region"]


def test_coerce_chroma_meta_json_encodes_dicts():
    from app.services.ingestion import _coerce_chroma_meta

    out = _coerce_chroma_meta({"h1": "Top", "h2": "Sub"})
    assert isinstance(out, str)
    assert json.loads(out) == {"h1": "Top", "h2": "Sub"}


def test_coerce_chroma_meta_normalises_sets_for_determinism():
    """Sets get sorted before JSON encoding so two equivalent sets
    produce the same JSON string (ChromaDB diff stability)."""
    from app.services.ingestion import _coerce_chroma_meta

    out_a = _coerce_chroma_meta({"b", "a", "c"})
    out_b = _coerce_chroma_meta({"c", "a", "b"})
    assert out_a == out_b
    assert json.loads(out_a) == ["a", "b", "c"]
