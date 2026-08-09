# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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

RAG Cycle C2.2: a leading YAML frontmatter block is stripped from the
source before header-splitting and its allowlisted keys are attached to
the FIRST emitted ``MarkdownSection`` element under
``metadata["frontmatter"]``.  The service layer picks the dict up from
the chunker output and threads it into Neo4j Artifact properties +
alias resolution.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from core.ingest.frontmatter import extract_frontmatter
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
    minus the file-IO step. Useful for stdin / streamed sources.

    RAG Cycle C2.2: if ``text`` starts with a ``---``-fenced YAML
    frontmatter block, the allowlisted keys are extracted and attached
    to the first emitted element under
    ``metadata["frontmatter_json"]`` (JSON-encoded so the value
    round-trips through ChromaDB's primitive-only metadata schema).
    The frontmatter fence itself is stripped before header-splitting so
    the splitter doesn't see ``---`` as a horizontal rule.
    """
    if not text.strip():
        return []

    # Strip frontmatter first so the header splitter never sees the
    # fence (langchain treats ``---`` as a thematic-break which would
    # split the body in unexpected places).  Empty frontmatter dict +
    # unchanged body when no fence is present.
    frontmatter, body = extract_frontmatter(text)

    if not body.strip():
        # Pure-frontmatter file with no body — emit a single empty
        # section so the frontmatter still reaches the service layer.
        if frontmatter:
            return [
                {
                    "text": "",
                    "element_type": "MarkdownSection",
                    "metadata": {
                        "heading_path": [],
                        "level": 0,
                        "headers": {},
                        "frontmatter_json": json.dumps(frontmatter),
                    },
                },
            ]
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
    docs = splitter.split_text(body)

    elements: list[ParsedElement] = []
    for doc in docs:
        body_text = doc.page_content
        if not body_text.strip():
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
                "text": body_text,
                "element_type": "MarkdownSection",
                "metadata": {
                    "heading_path": heading_path,
                    "level": level,
                    "headers": dict(headers),
                },
            },
        )

    # Attach frontmatter to the FIRST emitted element so the service
    # layer picks it up exactly once.  The chunker strategy propagates
    # element metadata into chunk metadata verbatim.
    if frontmatter and elements:
        elements[0]["metadata"]["frontmatter_json"] = json.dumps(frontmatter)

    logger.info(
        "markdown_parsed sections=%d max_depth=%d frontmatter_keys=%d",
        len(elements),
        max((el["metadata"]["level"] for el in elements), default=0),
        len(frontmatter),
    )
    return elements
