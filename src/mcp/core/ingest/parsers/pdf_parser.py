# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""PDF parser — page-level text + table-as-own-element extraction.

Workstream E Phase 2b.5. Closes the audit's "PDF: tables ignored;
no layout awareness" gap. Each page becomes:

* one :class:`NarrativeText` element per page — page text minus
  detected tables (tables are emitted separately so they don't
  dominate the page chunk's embedded representation)
* one :class:`Table` element per detected table on the page —
  Markdown-pipe rendered text + a structured ``rows`` list in
  metadata for retrieval that wants the table cells directly

Library choice: **pdfplumber** — already in this codebase's
``requirements.txt`` (used by the legacy parser at
``app/parsers/pdf.py``). Has table extraction + per-page text +
no heavy ML stack — works without `unstructured[all-docs]`'s
~600MB layout-model footprint. The Pro-tier ``hi_res`` strategy
would land via ``unstructured`` in a future commit; this commit
ships the community-tier "fast" path that operators can opt into
on every deployment.

Phase 2b.5 doesn't ship OCR — image-only PDFs return empty text.
The legacy ``app/parsers/pdf.py`` already calls ``pytesseract`` as
an optional fallback; integrating that into this parser is queued
for the same follow-up that adds Pro-tier ``hi_res``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.ingest.parsers import ParsedElement

logger = logging.getLogger("ai-companion.ingest.parsers.pdf")


def _table_rows_to_markdown(rows: list[list[str | None]]) -> str:
    """Render a 2D cell grid as a Markdown pipe-table.

    The first row is treated as the header; if there's only one row
    we just emit pipes (no separator line) so retrieval still gets
    the cell content. None / empty cells are rendered as empty
    strings — pdfplumber returns None for un-detected cells.
    """
    if not rows:
        return ""
    cleaned = [[(c or "").strip() for c in row] for row in rows]
    width = max(len(r) for r in cleaned)
    cleaned = [r + [""] * (width - len(r)) for r in cleaned]

    lines: list[str] = []
    if len(cleaned) >= 1:
        lines.append("| " + " | ".join(cleaned[0]) + " |")
    if len(cleaned) >= 2:
        lines.append("| " + " | ".join(["---"] * width) + " |")
    for row in cleaned[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def parse_pdf(path: str | Path) -> list[ParsedElement]:
    """Parse a PDF file into NarrativeText + Table elements.

    Args:
        path: Filesystem path to the `.pdf` file.

    Returns:
        A list of :class:`ParsedElement` dicts. One ``NarrativeText``
        per page (skipped if the page is empty); one ``Table`` per
        detected table. Each carries ``page_num`` in metadata; tables
        also carry ``n_rows``, ``n_cols``, and a structured
        ``rows: list[list[str]]``.

        Returns ``[]`` for empty PDFs or PDFs with unreachable text
        (image-only without OCR — caller falls through to the legacy
        flat-text parser which has the OCR path).

    Raises:
        FileNotFoundError: when ``path`` doesn't exist.
        ImportError: when pdfplumber isn't installed.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"PDF not found: {p}")

    # Lazy import — keep module loadable when pdfplumber is missing.
    import pdfplumber

    elements: list[ParsedElement] = []
    try:
        with pdfplumber.open(p) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                # Extract tables FIRST — pdfplumber's text extractor
                # includes table cells in page.extract_text() output,
                # so we strip table-cell text from the page narrative
                # to avoid double-embedding.
                tables_raw = page.extract_tables() or []
                table_cells: set[str] = set()
                for table in tables_raw:
                    if not table:
                        continue
                    n_rows = len(table)
                    n_cols = max((len(r) for r in table), default=0)
                    table_text = _table_rows_to_markdown(table)
                    if not table_text.strip():
                        continue
                    elements.append(
                        {
                            "text": table_text,
                            "element_type": "Table",
                            "metadata": {
                                "page_num": page_num,
                                "n_rows": n_rows,
                                "n_cols": n_cols,
                                "rows": [
                                    [(c or "").strip() for c in row]
                                    for row in table
                                ],
                            },
                        },
                    )
                    for row in table:
                        for cell in row:
                            if cell:
                                table_cells.add(cell.strip())

                page_text = (page.extract_text() or "").strip()
                if page_text:
                    # Best-effort table-cell strip: drop lines that are
                    # entirely a single table cell. Multi-cell rows tend
                    # to pdfplumber-render as one space-separated line
                    # which doesn't match exactly — accepted minor
                    # double-coverage in those cases.
                    if table_cells:
                        page_text = "\n".join(
                            line for line in page_text.split("\n")
                            if line.strip() and line.strip() not in table_cells
                        )

                    if page_text.strip():
                        elements.append(
                            {
                                "text": page_text,
                                "element_type": "NarrativeText",
                                "metadata": {
                                    "page_num": page_num,
                                },
                            },
                        )
    except Exception as exc:  # noqa: BLE001 — fall back to legacy parser
        logger.warning("pdf_parse_failed file=%s error=%s", p.name, exc)
        return []

    logger.info(
        "pdf_parsed file=%s elements=%d (tables=%d)",
        p.name, len(elements),
        sum(1 for el in elements if el["element_type"] == "Table"),
    )
    return elements


def _expose_helper_for_tests(*, _internal: Any = None) -> Any:
    """Re-export ``_table_rows_to_markdown`` for tests without making
    it part of the module's public API."""
    return _table_rows_to_markdown
