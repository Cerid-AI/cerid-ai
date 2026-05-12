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

    Wikilinks (``[[Target]]`` / ``![[Embed]]``) discovered in the
    section body are emitted as additional zero-text
    :data:`WikilinkEdge` chunks appended after the text chunks.  The
    graph-commit step in ``app/services/ingestion.py`` translates each
    one into a Neo4j ``WIKILINKS_TO`` (or ``EMBEDS``) edge.  This
    mirrors the ``EmailThreadEdge`` pattern.
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
        text_chunks: list[dict[str, Any]] = [
            {
                "text": combined,
                "metadata": {
                    "element_type": "MarkdownSection",
                    **metadata,
                },
            },
        ]
    else:
        # Body too large: split into token-bounded pieces, re-prepend
        # the breadcrumb on each so the structural anchor sticks to
        # every chunk.
        pieces = chunk_text(body, max_tokens=max_tokens)
        text_chunks = [
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

    edge_chunks = markdown_wikilink_edge_strategy(element)
    return text_chunks + edge_chunks


def markdown_wikilink_edge_strategy(element: ParsedElement) -> list[dict[str, Any]]:
    """Emit zero-text ``WikilinkEdge`` chunks for each unique wikilink.

    The body is scanned with :func:`core.ingest.wikilinks.extract_wikilinks`
    which handles fenced-code-block and inline-code exclusion and the
    ``(target, heading, alias, is_embed)`` dedup.

    Each emitted chunk carries metadata only:

    ``element_type``
        Always ``"WikilinkEdge"``.
    ``wikilink_target``
        The link target (filename stem to resolve, see C2.1 Phase C).
    ``wikilink_alias``
        Display alias (defaults to ``target`` if not supplied).
    ``wikilink_heading``
        Heading anchor or empty string.
    ``wikilink_is_embed``
        ``"true"`` / ``"false"`` — string form for ChromaDB compatibility
        (its metadata schema is ``str | int | float | bool | None`` and
        bool round-trips poorly when the column is later string-typed).
    ``wikilink_source_chunk_idx``
        Stringified ``0`` — points at the first text chunk produced by
        the surrounding ``markdown_section_strategy`` call for this
        element.  The full chunk id (``"{artifact_id}_chunk_{idx}"``) is
        recomposed at graph-write time once the artifact_id is known.

    Returns ``[]`` when the body contains no wikilinks.
    """
    from core.ingest.wikilinks import extract_wikilinks

    body = element.get("text", "")
    if not body:
        return []
    refs = extract_wikilinks(body)
    if not refs:
        return []
    # All wikilinks in this element originated from the first text
    # chunk emitted alongside it (section_chunk_idx=0 in the split
    # case, or the only chunk in the single-chunk case).  We don't try
    # to map links to their split sub-chunk — retrieval-grain
    # provenance is not part of C2.1's contract.
    source_chunk_idx = "0"
    return [
        {
            "text": "",
            "metadata": {
                "element_type": "WikilinkEdge",
                "wikilink_target": ref.target,
                "wikilink_alias": ref.alias,
                "wikilink_heading": ref.heading,
                "wikilink_is_embed": "true" if ref.is_embed else "false",
                "wikilink_source_chunk_idx": source_chunk_idx,
            },
        }
        for ref in refs
    ]


def register_default_strategies() -> None:
    """Register Phase 2b.2 strategies on the chunker registry."""
    from core.ingest.chunkers import register

    register("MarkdownSection", markdown_section_strategy)
