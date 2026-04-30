# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Markdown section chunker — leaf section + heading-path replay.

Workstream E Phase 2b.2. Each :class:`MarkdownSection` element from
the parser becomes one chunk whose text re-prepends the heading path
('# Getting Started > ## Installation > ### Steps') so a query for
'installation' can match even when the literal word is only in the
heading and not the section body.

If a section's body is longer than ``MAX_CHUNK_TOKENS`` (which can
happen for long un-subsectioned bodies), the strategy splits via
the legacy token chunker AND re-prepends the heading path on each
sub-chunk so retrieval doesn't lose the structural anchor.
"""
from __future__ import annotations

from typing import Any

import config
from core.ingest.parsers import ParsedElement


def _heading_breadcrumb(heading_path: list[str]) -> str:
    """Render the heading path as a single-line breadcrumb prefix."""
    if not heading_path:
        return ""
    return " > ".join(heading_path)


def markdown_section_strategy(element: ParsedElement) -> list[dict[str, Any]]:
    """Emit one chunk per section with the heading-path prepended.

    Splits on token budget when the body is long; every sub-chunk
    keeps the same heading_path metadata so retrieval can group
    them back if needed.
    """
    body = element["text"]
    metadata = element.get("metadata", {})
    heading_path = list(metadata.get("heading_path", []))
    breadcrumb = _heading_breadcrumb(heading_path)

    # Lazy import to dodge a potential circular when chunker registry
    # is imported during config bootstrap.
    from utils.chunker import chunk_text, count_tokens

    max_tokens = getattr(config, "PARENT_CHUNK_TOKENS", 512)

    # Combine breadcrumb + body for token budgeting; if it fits we
    # emit a single chunk, otherwise the body is split and the
    # breadcrumb re-prepended on each piece.
    combined = f"{breadcrumb}\n\n{body}" if breadcrumb else body

    if count_tokens(combined) <= max_tokens:
        return [
            {
                "text": combined,
                "metadata": {
                    "element_type": "MarkdownSection",
                    **metadata,
                },
            },
        ]

    # Body too large: split into token-bounded pieces, re-prepend
    # the breadcrumb on each so the structural anchor sticks to
    # every chunk.
    pieces = chunk_text(body, max_tokens=max_tokens)
    return [
        {
            "text": f"{breadcrumb}\n\n{piece}" if breadcrumb else piece,
            "metadata": {
                "element_type": "MarkdownSection",
                "section_chunk_idx": idx,
                **metadata,
            },
        }
        for idx, piece in enumerate(pieces)
    ]


def register_default_strategies() -> None:
    """Register Phase 2b.2 strategies on the chunker registry."""
    from core.ingest.chunkers import register

    register("MarkdownSection", markdown_section_strategy)
