# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the CSV parser + row-replay strategy (Workstream E Phase 2b.1).

Synthetic fixtures cover the audit gap directly: column headers must
propagate to every chunk so quantitative queries can match on column
names. Per locked Decision #1 the public-corpus parser fixtures are a
follow-up — these synthetic cases prove the contract on every PR.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.ingest.chunkers import chunk_elements
from core.ingest.chunkers.csv_strategy import csv_row_strategy
from core.ingest.parsers.csv_parser import parse_csv


def _write_csv(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_csv_emits_one_element_per_row(tmp_path):
    p = _write_csv(
        tmp_path, "sales.csv",
        "quarter,revenue,region\nQ1,1500000,EMEA\nQ2,1800000,APAC\nQ3,2100000,AMER\n",
    )
    elements = parse_csv(p)
    assert len(elements) == 3
    for el in elements:
        assert el["element_type"] == "CSVRow"
        # column_headers must propagate so retrieval can match on column names
        assert el["metadata"]["column_headers"] == ["quarter", "revenue", "region"]


def test_parse_csv_text_replays_headers(tmp_path):
    """The audit's headline gap: 'column1: value1 | column2: value2 | ...' format."""
    p = _write_csv(tmp_path, "sales.csv", "quarter,revenue\nQ1,1500000\n")
    elements = parse_csv(p)
    assert elements[0]["text"] == "quarter: Q1 | revenue: 1500000"


def test_parse_csv_quoted_fields_with_commas(tmp_path):
    """Stdlib csv module handles quoting; the row-replay must too."""
    p = _write_csv(
        tmp_path, "complex.csv",
        'company,address\nAcme Corp,"123 Main St, Suite 4B"\n',
    )
    elements = parse_csv(p)
    assert len(elements) == 1
    assert elements[0]["metadata"]["cells"] == ["Acme Corp", "123 Main St, Suite 4B"]
    assert "123 Main St, Suite 4B" in elements[0]["text"]


def test_parse_csv_skips_empty_data_rows(tmp_path):
    """Wholly-empty rows in the body are silently dropped."""
    p = _write_csv(tmp_path, "sparse.csv", "id,name\n1,alice\n,\n2,bob\n")
    elements = parse_csv(p)
    assert len(elements) == 2
    assert [el["metadata"]["cells"][1] for el in elements] == ["alice", "bob"]


def test_parse_csv_pads_short_rows(tmp_path):
    """Rows shorter than header pad with empty strings — common in real data."""
    p = _write_csv(tmp_path, "short.csv", "a,b,c\n1,2\n")
    elements = parse_csv(p)
    assert elements[0]["metadata"]["cells"] == ["1", "2", ""]
    assert "c: " in elements[0]["text"]


def test_parse_csv_truncates_long_rows(tmp_path):
    """Rows longer than header are truncated — defensive against malformed CSV."""
    p = _write_csv(tmp_path, "long.csv", "a,b\n1,2,3,4\n")
    elements = parse_csv(p)
    assert elements[0]["metadata"]["cells"] == ["1", "2"]


def test_parse_csv_empty_file_returns_empty(tmp_path):
    p = _write_csv(tmp_path, "empty.csv", "")
    assert parse_csv(p) == []


def test_parse_csv_header_only_returns_empty(tmp_path):
    """Header without data rows is a valid CSV but yields no elements."""
    p = _write_csv(tmp_path, "header_only.csv", "a,b,c\n")
    assert parse_csv(p) == []


def test_parse_csv_assigns_one_indexed_row_idx(tmp_path):
    """row_idx counts data rows starting at 1 (header is row 0)."""
    p = _write_csv(tmp_path, "rows.csv", "a\n1\n2\n3\n")
    elements = parse_csv(p)
    assert [el["metadata"]["row_idx"] for el in elements] == [1, 2, 3]


def test_parse_csv_strips_whitespace_around_cells(tmp_path):
    """Trailing spaces on values shouldn't bleed into the embedded text."""
    p = _write_csv(tmp_path, "spaced.csv", "name, value \n  alice  ,  42  \n")
    elements = parse_csv(p)
    assert elements[0]["metadata"]["cells"] == ["alice", "42"]
    assert "name: alice" in elements[0]["text"]


def test_parse_csv_raises_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_csv(tmp_path / "does_not_exist.csv")


def test_parse_csv_handles_utf8_bom_encoding(tmp_path):
    """Excel exports often add a BOM; utf-8-sig encoding reads them cleanly."""
    p = tmp_path / "bom.csv"
    p.write_bytes("﻿header1,header2\nval1,val2\n".encode())
    elements = parse_csv(p, encoding="utf-8-sig")
    assert elements[0]["metadata"]["column_headers"] == ["header1", "header2"]


# ---------------------------------------------------------------------------
# Chunker strategy + dispatch
# ---------------------------------------------------------------------------


def test_csv_row_strategy_emits_one_chunk_per_element():
    element = {
        "text": "name: alice | age: 30",
        "element_type": "CSVRow",
        "metadata": {
            "row_idx": 1,
            "column_headers": ["name", "age"],
            "cells": ["alice", "30"],
        },
    }
    chunks = csv_row_strategy(element)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "name: alice | age: 30"
    # element_type is reasserted in the chunk metadata
    assert chunks[0]["metadata"]["element_type"] == "CSVRow"
    # column_headers must survive the dispatch — that's the whole point
    assert chunks[0]["metadata"]["column_headers"] == ["name", "age"]


def test_chunk_elements_dispatches_csv_row_strategy(tmp_path):
    """Importing the chunker package registers the CSV strategy
    (verified by the dispatcher producing one chunk per row, not
    token-chunked)."""
    p = _write_csv(tmp_path, "sales.csv", "q,rev\nQ1,100\nQ2,200\n")
    elements = parse_csv(p)
    chunks = chunk_elements(elements)
    assert len(chunks) == 2  # one chunk per row, NOT token-chunked
    assert all(c["metadata"]["element_type"] == "CSVRow" for c in chunks)
    # Headers must reach every chunk's metadata
    assert all(c["metadata"]["column_headers"] == ["q", "rev"] for c in chunks)


def test_chunk_elements_csv_chunks_carry_full_metadata(tmp_path):
    p = _write_csv(tmp_path, "sales.csv", "q,rev\nQ1,100\n")
    chunks = chunk_elements(parse_csv(p))
    md = chunks[0]["metadata"]
    assert md["row_idx"] == 1
    assert md["cells"] == ["Q1", "100"]
