# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""CSV parser — row-as-element with header-replay context.

Workstream E Phase 2b.1 — fixes the audit-flagged critical gap
("CSV: column headers parsed but never propagated to chunk metadata;
cells become flat text — quantitative queries blocked").

Each non-empty data row becomes one :class:`CSVRow` element whose
``text`` is the row formatted as ``"col1: val1 | col2: val2 | ..."``
so the embedded representation carries both column semantics and
values. Downstream the row-replay chunker emits one chunk per row,
preserving the column_headers in metadata for filtering.

Library independence: stdlib ``csv`` only — no `unstructured` /
`docling` dependency. The format-agnostic :class:`ParsedElement`
contract from :mod:`core.ingest.parsers` lets a future Phase 2c swap
to a typed CSV library (polars / pandas) without touching the
chunker layer.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path

from core.ingest.parsers import ParsedElement

logger = logging.getLogger("ai-companion.ingest.parsers.csv")


def parse_csv(path: str | Path, *, encoding: str = "utf-8") -> list[ParsedElement]:
    """Parse a CSV file into ``CSVRow`` elements with column-replayed text.

    Args:
        path: Filesystem path to the CSV file.
        encoding: File encoding (default utf-8). Pass ``"utf-8-sig"``
            for files that may carry a BOM (Excel exports often do).

    Returns:
        A list of :class:`ParsedElement` dicts, one per non-empty data
        row. Each element carries:

        * ``text`` — ``"col1: val1 | col2: val2 | ..."``
        * ``element_type`` — ``"CSVRow"``
        * ``metadata`` — ``{row_idx, column_headers, cells}``

        Returns ``[]`` for empty files or files containing only a
        header row.

    Raises:
        FileNotFoundError: when ``path`` doesn't exist.
        UnicodeDecodeError: when the file isn't decodable in the given
            encoding (caller must catch + retry with a different encoding).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CSV not found: {p}")

    with p.open(newline="", encoding=encoding) as f:
        reader = csv.reader(f)
        rows = [row for row in reader if row]  # skip wholly-empty rows

    if not rows:
        return []

    headers = [h.strip() for h in rows[0]]
    if len(rows) == 1:
        # Header-only file — no data to emit.
        return []

    elements: list[ParsedElement] = []
    for idx, row in enumerate(rows[1:], start=1):
        # Defensive: pad shorter rows, truncate longer ones to header width.
        cells = list(row[: len(headers)])
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))

        cells = [c.strip() for c in cells]

        # Skip rows where every cell is empty after strip
        if not any(cells):
            continue

        # Header-replay: each row's embedded text carries the column
        # semantics so retrieval can match on column names too.
        text = " | ".join(f"{h}: {v}" for h, v in zip(headers, cells))

        elements.append(
            {
                "text": text,
                "element_type": "CSVRow",
                "metadata": {
                    "row_idx": idx,
                    "column_headers": headers,
                    "cells": cells,
                },
            },
        )

    logger.info(
        "csv_parsed file=%s rows=%d headers=%d",
        p.name, len(elements), len(headers),
    )
    return elements
