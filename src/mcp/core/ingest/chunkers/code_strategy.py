# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Code chunker — function/class-bounded chunks with file-path replay.

Workstream E Phase 2b.3. Each :class:`CodeFunction` /
:class:`CodeClass` / :class:`CodeImport` element from the AST parser
becomes one chunk. The chunk text is prefixed with a file-path +
qualified-name breadcrumb ('# src/mcp/foo.py :: MyClass') so a
retrieval query for "MyClass" or for the file path itself can match.

Long function/class bodies (rare but possible — generated code,
huge config classes) get split via the legacy token chunker AND
the breadcrumb re-prepended on each piece, mirroring the Markdown
strategy's pattern so the structural anchor sticks across sub-chunks.
"""
from __future__ import annotations

from typing import Any

import config
from core.ingest.parsers import ParsedElement


def _code_breadcrumb(metadata: dict[str, Any]) -> str:
    """Render '# <file> :: <name>' for prepending to chunk text.

    The leading '#' renders as a Python comment (and Markdown heading)
    so the breadcrumb doesn't break syntax-aware downstream tooling.
    Imports use the file path only (no name to qualify).
    """
    file_path = metadata.get("file", "")
    name = metadata.get("qualified_name") or metadata.get("name", "")
    if not file_path:
        return ""
    if not name:
        return f"# {file_path}"
    return f"# {file_path} :: {name}"


def code_chunk_strategy(element: ParsedElement) -> list[dict[str, Any]]:
    """Emit one chunk per function/class/import with a file-path breadcrumb.

    Splits oversized bodies on the token budget, re-prepending the
    breadcrumb on each sub-chunk.
    """
    body = element["text"]
    metadata = element.get("metadata", {})
    breadcrumb = _code_breadcrumb(metadata)
    element_type = element["element_type"]

    from utils.chunker import chunk_text, count_tokens

    max_tokens = getattr(config, "PARENT_CHUNK_TOKENS", 512)

    combined = f"{breadcrumb}\n\n{body}" if breadcrumb else body

    if count_tokens(combined) <= max_tokens:
        return [
            {
                "text": combined,
                "metadata": {
                    "element_type": element_type,
                    **metadata,
                },
            },
        ]

    # Oversized body: split, re-prepend breadcrumb, tag chunk index.
    pieces = chunk_text(body, max_tokens=max_tokens)
    return [
        {
            "text": f"{breadcrumb}\n\n{piece}" if breadcrumb else piece,
            "metadata": {
                "element_type": element_type,
                "code_chunk_idx": idx,
                **metadata,
            },
        }
        for idx, piece in enumerate(pieces)
    ]


def register_default_strategies() -> None:
    """Register Phase 2b.3 strategies — same dispatcher for all three
    code element types since the formatting is identical."""
    from core.ingest.chunkers import register

    for et in ("CodeFunction", "CodeClass", "CodeImport"):
        register(et, code_chunk_strategy)
