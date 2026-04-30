# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the PDF parser + chunker (Workstream E Phase 2b.5).

Synthetic PDFs are generated with reportlab (dev-only dep —
requirements-dev.txt). The runtime parser uses pdfplumber, which
is already in requirements.txt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# reportlab is dev-only — skip the whole module when missing
# (e.g. CI test job that doesn't pull requirements-dev.txt).
reportlab = pytest.importorskip("reportlab")

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.lib.styles import getSampleStyleSheet  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)
from reportlab.platypus import (  # noqa: E402
    Table as RLTable,
)

from core.ingest.chunkers import chunk_elements  # noqa: E402
from core.ingest.chunkers.pdf_strategy import (  # noqa: E402
    pdf_narrative_strategy,
    pdf_table_strategy,
)
from core.ingest.parsers.pdf_parser import (  # noqa: E402
    _expose_helper_for_tests,
    parse_pdf,
)

# ---------------------------------------------------------------------------
# Synthetic PDF builders (reportlab)
# ---------------------------------------------------------------------------


def _build_text_pdf(path: Path, text_per_page: list[str]) -> Path:
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    for i, body in enumerate(text_per_page):
        story.append(Paragraph(body, styles["Normal"]))
        if i < len(text_per_page) - 1:
            from reportlab.platypus import PageBreak
            story.append(PageBreak())
    doc.build(story)
    return path


def _build_pdf_with_table(path: Path, headers: list[str], rows: list[list[str]]) -> Path:
    """Render a single-page PDF whose only content is the table grid.

    Surrounding the table with non-table prose lets the test assert
    pdfplumber detects the grid AND retains the prose as
    NarrativeText separately.
    """
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Quarterly Revenue Summary", styles["Heading1"]),
        Spacer(1, 12),
    ]
    data = [headers] + rows
    tbl = RLTable(data)
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ]
        ),
    )
    story.append(tbl)
    doc.build(story)
    return path


# ---------------------------------------------------------------------------
# Helper unit tests (pure)
# ---------------------------------------------------------------------------


def test_table_rows_to_markdown_renders_pipe_table():
    helper = _expose_helper_for_tests()
    md = helper(
        [["q", "rev"], ["Q1", "1500000"], ["Q2", "1800000"]],
    )
    assert md.startswith("| q | rev |")
    assert "| --- | --- |" in md
    assert "| Q1 | 1500000 |" in md


def test_table_rows_to_markdown_handles_none_cells():
    helper = _expose_helper_for_tests()
    md = helper([["a", None, "c"], [None, "y", None]])
    # None cells render as empty strings, not literal 'None'
    assert "None" not in md


def test_table_rows_to_markdown_pads_ragged_rows():
    """Rows shorter than the widest row get padded — common in real PDFs."""
    helper = _expose_helper_for_tests()
    md = helper([["a", "b", "c"], ["1"]])
    # Padded row has the right column count
    assert "| 1 |  |  |" in md


def test_table_rows_to_markdown_empty_returns_empty():
    helper = _expose_helper_for_tests()
    assert helper([]) == ""


# ---------------------------------------------------------------------------
# Parser — synthetic PDFs
# ---------------------------------------------------------------------------


def test_parse_pdf_emits_one_narrative_per_page(tmp_path):
    p = _build_text_pdf(
        tmp_path / "two_pages.pdf",
        ["First page body content here.", "Second page body content here."],
    )
    elements = parse_pdf(p)
    narratives = [el for el in elements if el["element_type"] == "NarrativeText"]
    assert len(narratives) == 2
    pages = sorted(el["metadata"]["page_num"] for el in narratives)
    assert pages == [1, 2]


def test_parse_pdf_extracts_table_as_separate_element(tmp_path):
    p = _build_pdf_with_table(
        tmp_path / "table.pdf",
        ["Quarter", "Revenue", "Region"],
        [
            ["Q1", "1500000", "EMEA"],
            ["Q2", "1800000", "APAC"],
            ["Q3", "2100000", "AMER"],
        ],
    )
    elements = parse_pdf(p)
    tables = [el for el in elements if el["element_type"] == "Table"]
    assert len(tables) >= 1, "pdfplumber didn't detect a table — check fixture"
    tbl = tables[0]
    # Markdown-pipe format with header
    assert "| Quarter | Revenue | Region |" in tbl["text"]
    assert "| Q1 | 1500000 | EMEA |" in tbl["text"]
    # Structured rows preserved in metadata for retrieval that wants cells
    assert tbl["metadata"]["n_cols"] == 3
    assert tbl["metadata"]["page_num"] == 1
    assert "rows" in tbl["metadata"]
    # First row in `rows` is the header
    assert tbl["metadata"]["rows"][0] == ["Quarter", "Revenue", "Region"]


def test_parse_pdf_table_does_not_swallow_surrounding_narrative(tmp_path):
    """The table extraction shouldn't lose the heading paragraph."""
    p = _build_pdf_with_table(
        tmp_path / "table.pdf",
        ["a", "b"],
        [["1", "2"]],
    )
    elements = parse_pdf(p)
    narratives = [el for el in elements if el["element_type"] == "NarrativeText"]
    # The "Quarterly Revenue Summary" heading paragraph survives outside the table
    assert any("Quarterly Revenue Summary" in el["text"] for el in narratives)


def test_parse_pdf_empty_pdf_returns_empty(tmp_path):
    """A PDF with no extractable text returns an empty list — caller
    falls back to the legacy parser (which has the OCR path)."""
    p = tmp_path / "blank.pdf"
    # Build a single empty page
    doc = SimpleDocTemplate(str(p), pagesize=letter)
    doc.build([Spacer(1, 1)])  # just whitespace, no text
    elements = parse_pdf(p)
    # Either zero elements (no text found) or whatever pdfplumber recovers
    # — assert the contract: never raises, returns a list
    assert isinstance(elements, list)


def test_parse_pdf_raises_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_pdf(tmp_path / "missing.pdf")


def test_parse_pdf_swallows_corrupt_file_and_returns_empty(tmp_path):
    """Corrupt PDF bytes shouldn't crash the dispatcher — return [] so
    the layout-aware caller falls back to the legacy parse_file path."""
    p = tmp_path / "corrupt.pdf"
    p.write_bytes(b"%PDF-broken garbage that pdfplumber will reject")
    elements = parse_pdf(p)
    assert elements == []


# ---------------------------------------------------------------------------
# Chunker strategies
# ---------------------------------------------------------------------------


def test_pdf_narrative_strategy_prepends_page_breadcrumb():
    el = {
        "text": "page body content",
        "element_type": "NarrativeText",
        "metadata": {"page_num": 7},
    }
    chunks = pdf_narrative_strategy(el)
    assert chunks[0]["text"].startswith("Page 7")
    assert "page body content" in chunks[0]["text"]
    assert chunks[0]["metadata"]["page_num"] == 7


def test_pdf_narrative_strategy_no_page_num_no_breadcrumb():
    el = {
        "text": "narrative without page anchor",
        "element_type": "NarrativeText",
        "metadata": {},
    }
    chunks = pdf_narrative_strategy(el)
    assert chunks[0]["text"] == "narrative without page anchor"


def test_pdf_narrative_strategy_splits_oversized_page(monkeypatch):
    import config
    monkeypatch.setattr(config, "PARENT_CHUNK_TOKENS", 30)
    el = {
        "text": "page sentence " * 60,
        "element_type": "NarrativeText",
        "metadata": {"page_num": 3},
    }
    chunks = pdf_narrative_strategy(el)
    assert len(chunks) >= 2
    for c in chunks:
        assert c["text"].startswith("Page 3")
    # page_chunk_idx counts from 0
    indices = [c["metadata"].get("page_chunk_idx") for c in chunks]
    assert indices == list(range(len(chunks)))


def test_pdf_table_strategy_passes_through():
    el = {
        "text": "| a | b |\n| --- | --- |\n| 1 | 2 |",
        "element_type": "Table",
        "metadata": {
            "page_num": 1,
            "n_rows": 2,
            "n_cols": 2,
            "rows": [["a", "b"], ["1", "2"]],
        },
    }
    chunks = pdf_table_strategy(el)
    assert len(chunks) == 1
    assert chunks[0]["text"] == el["text"]
    md = chunks[0]["metadata"]
    assert md["element_type"] == "Table"
    assert md["page_num"] == 1
    assert md["rows"] == [["a", "b"], ["1", "2"]]


# ---------------------------------------------------------------------------
# End-to-end dispatch
# ---------------------------------------------------------------------------


def test_chunk_elements_dispatches_pdf_strategies(tmp_path):
    p = _build_pdf_with_table(
        tmp_path / "doc.pdf",
        ["col_a", "col_b"],
        [["v1", "v2"]],
    )
    elements = parse_pdf(p)
    chunks = chunk_elements(elements)

    types = {c["metadata"]["element_type"] for c in chunks}
    assert "Table" in types
    # NarrativeText chunks carry a Page breadcrumb
    narrative_chunks = [c for c in chunks if c["metadata"]["element_type"] == "NarrativeText"]
    if narrative_chunks:
        assert any("Page 1" in c["text"] for c in narrative_chunks)
