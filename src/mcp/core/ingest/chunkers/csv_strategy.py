# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""CSV row chunker — one chunk per row with header-replayed text.

Workstream E Phase 2b.1. The :func:`csv_row_strategy` strategy is
register()-ed against the ``CSVRow`` element type so the dispatcher
emits one chunk per row instead of token-chunking the row text
(which the default fallback would do — destroying the row boundary).

Header replay happens at parse time (see
:func:`core.ingest.parsers.csv_parser.parse_csv`) — the strategy
just re-shapes the element into the chunk dict the downstream
metadata writer expects. Splitting wide rows into column groups when
they exceed the embedder's context budget is a future enhancement;
today the strategy emits one chunk per row regardless of width.
"""
from __future__ import annotations

from typing import Any

from core.ingest.parsers import ParsedElement


def csv_row_strategy(element: ParsedElement) -> list[dict[str, Any]]:
    """Emit one chunk for the row, preserving the parser's metadata.

    Returns a single-item list rather than the row's ``text`` directly
    so the dispatch shape (``list[dict[str, Any]]``) matches the rest
    of the chunker registry.
    """
    return [
        {
            "text": element["text"],
            "metadata": {
                "element_type": "CSVRow",
                **element.get("metadata", {}),
            },
        },
    ]


def register_default_strategies() -> None:
    """Register Phase 2b.1 strategies on the chunker registry.

    Idempotent: re-registration overwrites silently. Called from
    :mod:`core.ingest.chunkers` package init so the dispatch table
    is populated as soon as anything imports the chunker registry.
    """
    from core.ingest.chunkers import register

    register("CSVRow", csv_row_strategy)
