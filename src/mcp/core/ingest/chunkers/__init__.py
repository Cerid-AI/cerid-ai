# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Chunker registry — element-typed dispatch (Workstream E Phase 2a stub).

Phase 2a ships the dispatch contract; Phase 2b lands the per-element
strategies once the public-corpus test fixtures are in place
(locked Decision #1: "1-2 days to assemble"). The fallback strategy
forwards anything we don't yet have a specialised chunker for to the
existing token chunker, so the registry can ship today without
breaking ingest.

Dispatch table (Phase 2b will populate the right-hand side):

| element_type            | strategy                         |
|-------------------------|----------------------------------|
| Title / NarrativeText   | semantic chunker (paragraph)     |
| ListItem                | semantic chunker                 |
| Table                   | one chunk per table; HTML kept   |
| MarkdownSection         | one chunk per leaf section       |
| XLSXRow / CSVRow        | row-replay (header + body)       |
| EmailHeader / EmailBody | per-email chunk; thread metadata |
| CodeFunction / Class    | one chunk per function/class     |
| Image / Other / fallback| token chunker                    |
"""
from __future__ import annotations

import logging
from typing import Any

from core.ingest.parsers import ElementType, ParsedElement

logger = logging.getLogger("ai-companion.ingest.chunkers")

# Per-element strategies populate this registry as Phase 2b sub-phases
# land. Phase 2a shipped the empty registry; Phase 2b.1 registers
# CSVRow; later sub-phases register MarkdownSection, CodeFunction, etc.
_STRATEGIES: dict[ElementType, Any] = {}


def register(element_type: ElementType, strategy: Any) -> None:
    """Register a chunker strategy for an element type.

    Strategies are callables: ``(element: ParsedElement) -> list[Chunk]``.
    Phase 2b lands actual implementations; today this is the public
    seam Phase 2b commits will bind to.
    """
    if element_type in _STRATEGIES:
        logger.warning(
            "chunker strategy for %s already registered — overwriting",
            element_type,
        )
    _STRATEGIES[element_type] = strategy


def _register_phase_2b_strategies() -> None:
    """Auto-register strategies that ship in this codebase.

    Called once at import-time. Each strategy module owns its own
    register_default_strategies() so swapping a strategy is a
    per-module concern. Per-module failures are isolated — a broken
    code-AST module shouldn't take down CSV ingestion.
    """
    try:
        from core.ingest.chunkers.csv_strategy import register_default_strategies as _csv
        _csv()
    except Exception:  # noqa: BLE001 — registry import-time failures should never crash
        logger.exception("csv_strategy registration failed — falling back to default")

    try:
        from core.ingest.chunkers.markdown_strategy import register_default_strategies as _md
        _md()
    except Exception:  # noqa: BLE001
        logger.exception("markdown_strategy registration failed — falling back to default")

    try:
        from core.ingest.chunkers.code_strategy import register_default_strategies as _code
        _code()
    except Exception:  # noqa: BLE001
        logger.exception("code_strategy registration failed — falling back to default")

    try:
        from core.ingest.chunkers.email_strategy import register_default_strategies as _email
        _email()
    except Exception:  # noqa: BLE001
        logger.exception("email_strategy registration failed — falling back to default")

    try:
        from core.ingest.chunkers.pdf_strategy import register_default_strategies as _pdf
        _pdf()
    except Exception:  # noqa: BLE001
        logger.exception("pdf_strategy registration failed — falling back to default")

    try:
        from core.ingest.chunkers.xlsx_strategy import register_default_strategies as _xlsx
        _xlsx()
    except Exception:  # noqa: BLE001
        logger.exception("xlsx_strategy registration failed — falling back to default")


_register_phase_2b_strategies()


def chunk_elements(elements: list[ParsedElement]) -> list[dict[str, Any]]:
    """Dispatch a list of elements to their per-type chunker strategies.

    Phase 2a behaviour: when no strategy is registered for an
    element_type, fall back to the existing token chunker via
    ``utils.chunker.chunk_text`` so callers get sensible behaviour
    even before per-format strategies exist.

    Returns a flat list of dicts with at least
    ``{"text": str, "metadata": dict}`` so downstream
    services/ingestion.py can write to ChromaDB + BM25 + Neo4j
    without further transformation.
    """
    from utils.chunker import chunk_text

    out: list[dict[str, Any]] = []
    for element in elements:
        et: ElementType = element["element_type"]
        strategy = _STRATEGIES.get(et)
        if strategy is None:
            # Fallback: vanilla token chunking, preserve element metadata.
            for piece in chunk_text(element["text"]):
                out.append(
                    {
                        "text": piece,
                        "metadata": {
                            "element_type": et,
                            **element.get("metadata", {}),
                        },
                    },
                )
            continue
        out.extend(strategy(element))
    return out
