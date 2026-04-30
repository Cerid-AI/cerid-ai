# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""XLSX row chunker — one chunk per row with sheet+header replay.

Workstream E Phase 2b.6. Mirrors the CSV row-replay strategy from
Phase 2b.1 — the parser already shaped the row text with column
semantics; the strategy just re-shapes the element into the chunk
dict the downstream metadata writer expects, preserving the
``sheet_name`` so retrieval can scope to a workbook sheet.

Splitting wide rows that exceed the embedder's context budget is a
future enhancement; today the strategy emits one chunk per row
regardless of width (consistent with the CSV strategy).
"""
from __future__ import annotations

from typing import Any

from core.ingest.parsers import ParsedElement


def xlsx_row_strategy(element: ParsedElement) -> list[dict[str, Any]]:
    """Pass-through: one chunk per row, metadata propagates."""
    return [
        {
            "text": element["text"],
            "metadata": {
                "element_type": "XLSXRow",
                **element.get("metadata", {}),
            },
        },
    ]


def register_default_strategies() -> None:
    """Register Phase 2b.6 strategies on the chunker registry."""
    from core.ingest.chunkers import register

    register("XLSXRow", xlsx_row_strategy)
