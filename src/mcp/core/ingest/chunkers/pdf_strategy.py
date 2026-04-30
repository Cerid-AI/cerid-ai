# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""PDF chunker strategies (Workstream E Phase 2b.5).

* ``Table`` — single chunk per table; the markdown-pipe rendering
  IS the embed-ready text. Page number and structured ``rows``
  list propagate to chunk metadata so retrieval can filter to
  "tables on page 7" or pull the cell grid back without re-parsing.
* ``NarrativeText`` (PDF flavour) — per-page narrative gets a
  ``"Page <n>:"`` breadcrumb so retrieval can keep page anchors.
  Long pages split via the legacy token chunker with the breadcrumb
  replayed on each piece.

The PDF-specific NarrativeText strategy is registered alongside
the generic NarrativeText fallback (any future format that emits
NarrativeText elements without a ``page_num`` will simply skip the
breadcrumb and behave like the default token chunker).
"""
from __future__ import annotations

from typing import Any

import config
from core.ingest.parsers import ParsedElement


def _page_breadcrumb(metadata: dict[str, Any]) -> str:
    page_num = metadata.get("page_num")
    return f"Page {page_num}" if page_num else ""


def pdf_table_strategy(element: ParsedElement) -> list[dict[str, Any]]:
    """One chunk per table, markdown-pipe text preserved verbatim."""
    return [
        {
            "text": element["text"],
            "metadata": {
                "element_type": "Table",
                **element.get("metadata", {}),
            },
        },
    ]


def pdf_narrative_strategy(element: ParsedElement) -> list[dict[str, Any]]:
    """Page-narrative chunker — breadcrumb prepend, oversized split."""
    body = element["text"]
    metadata = element.get("metadata", {})
    breadcrumb = _page_breadcrumb(metadata)

    from utils.chunker import chunk_text, count_tokens

    max_tokens = getattr(config, "PARENT_CHUNK_TOKENS", 512)

    if not body:
        return []

    combined = f"{breadcrumb}\n\n{body}" if breadcrumb else body

    if count_tokens(combined) <= max_tokens:
        return [
            {
                "text": combined,
                "metadata": {
                    "element_type": "NarrativeText",
                    **metadata,
                },
            },
        ]

    pieces = chunk_text(body, max_tokens=max_tokens)
    return [
        {
            "text": f"{breadcrumb}\n\n{piece}" if breadcrumb else piece,
            "metadata": {
                "element_type": "NarrativeText",
                "page_chunk_idx": idx,
                **metadata,
            },
        }
        for idx, piece in enumerate(pieces)
    ]


def register_default_strategies() -> None:
    """Register Phase 2b.5 strategies on the chunker registry."""
    from core.ingest.chunkers import register

    register("Table", pdf_table_strategy)
    # NarrativeText with a page_num (PDF) gets the breadcrumb-prepend
    # path; without one (other formats) the same strategy degrades
    # gracefully — the breadcrumb is just empty.
    register("NarrativeText", pdf_narrative_strategy)
