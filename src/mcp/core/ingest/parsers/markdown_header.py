# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Markdown header-hierarchy parser (Workstream E Phase 2b.2).

Splits a Markdown document on heading boundaries (`#`, `##`, `###`)
and emits one :class:`MarkdownSection` element per leaf section. Each
element carries the full ordered heading path in its metadata, so a
retrieval query for "installation steps" can match a section under
``# Getting Started → ## Installation → ### Steps`` even though the
heading text never appears in the section body itself.

Library choice: `langchain_text_splitters.MarkdownHeaderTextSplitter`
— the de-facto standard for header-hierarchy chunking in 2026 RAG
(per the SOTA audit). Apache 2.0, ~30k★, single dependency. The
parser keeps the section text raw — header-prepending happens in the
chunker strategy so we can A/B test "with prepended headings" vs
"plain section text" without re-parsing.
"""
from __future__ import annotations

import logging
from pathlib import Path

from core.ingest.parsers import ParsedElement

logger = logging.getLogger("ai-companion.ingest.parsers.markdown")

# Header levels the splitter recognises. Match GitHub-flavoured
# Markdown convention; ATX-style only (no Setext == / -- underlines —
# those are rare in the docs we ingest).
_HEADERS_TO_SPLIT = [
    ("#", "h1"),
    ("##", "h2"),
    ("###", "h3"),
    ("####", "h4"),
    ("#####", "h5"),
    ("######", "h6"),
]
_HEADING_KEYS = ("h1", "h2", "h3", "h4", "h5", "h6")


def parse_markdown(path: str | Path, *, encoding: str = "utf-8") -> list[ParsedElement]:
    """Parse a Markdown file into ``MarkdownSection`` elements.

    Args:
        path: Filesystem path to a `.md` / `.markdown` file.
        encoding: File encoding (default utf-8).

    Returns:
        A list of :class:`ParsedElement` dicts, one per leaf section.
        Each element carries:

        * ``text`` — the section's raw body (no heading prefix)
        * ``element_type`` — ``"MarkdownSection"``
        * ``metadata`` — ``{heading_path, level, headers}``

          - ``heading_path``: ordered list of heading texts from h1
            down to the section's deepest header
          - ``level``: int 1-6 — the depth of the deepest heading
          - ``headers``: original mapping ``{h1: ..., h2: ...}``
            from the splitter (preserved for downstream tooling
            that wants the level→text dict directly)

        Returns ``[]`` for empty files or files with no headers
        (a heading-less Markdown file is treated as a single
        unstructured section that the default token chunker handles).

    Raises:
        FileNotFoundError: when ``path`` doesn't exist.
        ImportError: when langchain-text-splitters isn't installed.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Markdown not found: {p}")

    text = p.read_text(encoding=encoding)
    if not text.strip():
        return []

    return parse_markdown_string(text)


def parse_markdown_string(text: str) -> list[ParsedElement]:
    """Parse a Markdown string. Same contract as :func:`parse_markdown`
    minus the file-IO step. Useful for stdin / streamed sources."""
    if not text.strip():
        return []

    # Lazy import so the module loads even when the dep isn't installed —
    # the test harness can assert the ImportError surfaces cleanly.
    from langchain_text_splitters import MarkdownHeaderTextSplitter

    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=_HEADERS_TO_SPLIT,
        # strip_headers=True keeps section text body-only; re-attach in
        # chunker strategy if header-prepended retrieval is wanted.
        strip_headers=True,
    )
    docs = splitter.split_text(text)

    elements: list[ParsedElement] = []
    for doc in docs:
        body = doc.page_content
        if not body.strip():
            continue
        # Document.metadata is dict[str, str] keyed by h1..h6
        headers: dict[str, str] = doc.metadata
        # Build ordered heading_path from h1 → h6 (skip absent levels)
        heading_path = [headers[k] for k in _HEADING_KEYS if k in headers]
        # Depth = last present level, or 0 if no headers (paragraph before any heading)
        levels_present = [
            int(k[1:]) for k in _HEADING_KEYS if k in headers
        ]
        level = max(levels_present) if levels_present else 0

        elements.append(
            {
                "text": body,
                "element_type": "MarkdownSection",
                "metadata": {
                    "heading_path": heading_path,
                    "level": level,
                    "headers": dict(headers),
                },
            },
        )

    logger.info(
        "markdown_parsed sections=%d max_depth=%d",
        len(elements),
        max((el["metadata"]["level"] for el in elements), default=0),
    )
    return elements
