# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Parser protocol — format-agnostic element shape (Workstream E Phase 2a).

A parser is any callable that takes a file path and returns a list
of :class:`ParsedElement` dicts. Each element is one semantically-
distinct piece of the source document:

  - PDF:        Title, NarrativeText, Table, ListItem, Footer per page
  - DOCX:       sections by heading hierarchy + tables as standalone elements
  - XLSX:       one element per non-empty row (sheet_name + row_idx + headers in metadata)
  - CSV:        one element per row, headers replayed in metadata
  - Markdown:   one element per leaf section, heading_path in metadata
  - Email:      headers + body + (synthetic) thread-edge elements
  - Code:       tree-sitter-bounded function/class blocks

The chunker registry (sibling package) consumes this list and
dispatches per ``element_type``. Keeping the parser layer
format-aware and the chunker layer element-aware means a new
format adds one parser file without touching the chunker registry.

**Library independence:** this module deliberately defines no
``unstructured`` or ``docling`` types. Parser implementations can swap
between libraries without changing the cross-layer contract.
"""
from __future__ import annotations

from typing import Any, Literal, NotRequired, Protocol, TypedDict, runtime_checkable

# Canonical parser-version flag emitted alongside every ParsedElement
# list, so the registry shim can detect new- vs old-shape returns.
PARSER_VERSION = 2  # Phase 2 elements format

# The closed set of element_type values. Centralised so the chunker
# registry can dispatch with an exhaustive switch.
ElementType = Literal[
    "Title",
    "NarrativeText",
    "ListItem",
    "Table",
    "Footer",
    "Header",
    "PageBreak",
    "MarkdownSection",
    "XLSXRow",
    "CSVRow",
    "EmailHeader",
    "EmailBody",
    "EmailThreadEdge",
    "CodeFunction",
    "CodeClass",
    "CodeImport",
    "Image",
    "Other",
]


class ParsedElement(TypedDict):
    """One semantic unit emitted by a parser.

    The downstream chunker dispatches on ``element_type``; the
    ``metadata`` payload carries the format-specific fields the
    chunker (or the metadata writer that lands in ChromaDB / Neo4j)
    needs.
    """

    text: str
    """The element's textual content (the only field that gets embedded).

    Tables emit human-readable text here AND ``text_as_html`` in
    metadata. Empty-string text is allowed for thread-edge elements
    that contribute structure but no embeddable content.
    """

    element_type: ElementType
    """The semantic class — see :data:`ElementType` for the closed set."""

    metadata: NotRequired[dict[str, Any]]
    """Format-specific structured fields. Keys depend on element_type:

    * Title / NarrativeText / ListItem / Footer / Header
        ``{"page_num": int}`` (PDFs only)
    * Table
        ``{"page_num": int, "text_as_html": str, "n_rows": int, "n_cols": int}``
    * MarkdownSection
        ``{"heading_path": list[str], "level": int}``
    * XLSXRow
        ``{"sheet_name": str, "row_idx": int, "column_headers": list[str]}``
    * CSVRow
        ``{"row_idx": int, "column_headers": list[str]}``
    * EmailHeader
        ``{"from": str, "to": list[str], "cc": list[str], "subject": str,
           "date": iso_str, "message_id": str, "in_reply_to": str|None,
           "references": list[str], "thread_id": str}``
    * EmailBody
        ``{"thread_id": str, "message_id": str}``  (text already
        reply-stripped; quoted blocks dropped)
    * EmailThreadEdge
        ``{"thread_id": str, "message_id": str, "in_reply_to": str}``
    * CodeFunction / CodeClass / CodeImport
        ``{"file": str, "language": str, "name": str, "start_line": int,
           "end_line": int, "qualified_name": str}``
    """


@runtime_checkable
class ParserCallable(Protocol):
    """Format parser contract.

    Parsers are fully replaceable: the dispatcher only needs the
    callable shape. Library swaps (unstructured ↔ docling ↔ custom)
    are per-format file changes.
    """

    def __call__(self, path: str, /) -> list[ParsedElement]:
        ...


def is_legacy_return(value: object) -> bool:
    """Heuristic: distinguish old-shape ``(text, file_type, page_count)``
    or ``dict`` returns from new-shape ``list[ParsedElement]``.

    The registry shim in ``app/parsers/registry.py`` calls this to
    route the result to the right downstream path during the
    Phase 2a → 2b transition (both shapes coexist for one release).
    """
    if isinstance(value, list):
        return False
    return True


def is_parsed_elements(value: object) -> bool:
    """True iff ``value`` looks like a ``list[ParsedElement]``.

    Light-touch shape check (doesn't enforce metadata literal types
    — that would require runtime type validation and slow the hot
    parse path). Used by tests and the registry shim's assertions.
    """
    if not isinstance(value, list):
        return False
    if not value:
        return True  # empty parse is valid
    first = value[0]
    if not isinstance(first, dict):
        return False
    return "text" in first and "element_type" in first
