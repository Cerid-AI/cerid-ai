# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""F176 — TIERED_INFERENCE_ARCHITECTURE's code map must point at real code.

The two tables that claim to describe the *current* tree (§4.1 and Appendix B)
still used the pre-``core/`` layout and named a module that no longer exists,
so an operator following the doc to the rerank call site landed nowhere. Only
those two sections are gated — §5/§6 are the historical implementation plan
and are labelled as such in the doc.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_DOC = _REPO / "docs" / "TIERED_INFERENCE_ARCHITECTURE.md"
_SRC = _REPO / "src" / "mcp"


def _section(start: str, end: str | None) -> str:
    text = _DOC.read_text(encoding="utf-8")
    begin = text.index(start)
    stop = text.index(end, begin) if end else len(text)
    return text[begin:stop]


def _rows(section: str) -> list[list[str]]:
    rows = []
    for line in section.splitlines():
        if not line.startswith("|") or set(line) <= set("|- "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells and cells[0].startswith(("#", "File", "Test")):
            continue
        rows.append(cells)
    return rows


def _paths(cell: str) -> list[str]:
    return re.findall(r"`([\w./-]+\.py)(?::[\d\s,]+)?`", cell)


def test_offloading_matrix_points_at_live_code():
    """§4.1 — every row's file exists and contains the symbol it names."""
    broken: list[str] = []
    for cells in _rows(_section("### 4.1 Complete Matrix", "### 4.2")):
        symbol = re.sub(r"[`()]", "", cells[1]).strip()
        paths = _paths(cells[2])
        assert paths, f"row {cells[1]} names no source file"
        for rel in paths:
            target = _SRC / rel
            if not target.exists():
                broken.append(f"{rel} (missing) for {symbol}")
            elif symbol and symbol not in target.read_text(encoding="utf-8"):
                broken.append(f"{rel} no longer defines {symbol}")
    assert not broken, "§4.1 offloading matrix is stale: " + "; ".join(broken)


def test_file_reference_map_points_at_live_code():
    """Appendix B — every referenced module exists."""
    section = _section("## Appendix B: File Reference Map", None)
    missing = [
        rel
        for cells in _rows(section)
        for rel in _paths(cells[0])
        if not (_SRC / rel).exists()
    ]
    assert not missing, "Appendix B references modules that do not exist: " + ", ".join(missing)
