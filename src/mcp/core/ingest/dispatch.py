# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layout-aware ingest dispatcher (Workstream E Phase 2b wire-in).

Routes a file by extension to the appropriate
:mod:`core.ingest.parsers` module, runs it through the chunker
registry, and returns the result alongside the canonical raw text
(used by the upstream service for content_hash, AI categorization,
and Neo4j artifact metadata). When no Phase 2b parser claims the
extension the dispatcher returns ``(None, None)`` so the caller can
fall through to the legacy flat-text parser.

Adding a new format in a future sub-phase is one entry in
:data:`_DISPATCH` plus the parser file — the chunker strategy
self-registers at package import.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.ingest.chunkers import chunk_elements
from core.ingest.parsers import ParsedElement

logger = logging.getLogger("ai-companion.ingest.dispatch")


def _dispatch_csv(path: Path) -> list[ParsedElement]:
    from core.ingest.parsers.csv_parser import parse_csv
    return parse_csv(path)


def _dispatch_markdown(path: Path) -> list[ParsedElement]:
    from core.ingest.parsers.markdown_header import parse_markdown
    return parse_markdown(path)


def _dispatch_code(path: Path) -> list[ParsedElement]:
    from core.ingest.parsers.code_ast import parse_code
    return parse_code(path)


def _dispatch_email(path: Path) -> list[ParsedElement]:
    from core.ingest.parsers.email_parser import parse_email
    return parse_email(path)


def _dispatch_pdf(path: Path) -> list[ParsedElement]:
    from core.ingest.parsers.pdf_parser import parse_pdf
    return parse_pdf(path)


def _dispatch_xlsx(path: Path) -> list[ParsedElement]:
    from core.ingest.parsers.xlsx_parser import parse_xlsx
    return parse_xlsx(path)


def _dispatch_docx(path: Path) -> list[ParsedElement]:
    from core.ingest.parsers.docx_parser import parse_docx
    return parse_docx(path)


# Extension → parser callable. Future sub-phases (HTML)
# add entries here once the corresponding core/ingest/parsers/ module
# lands. Keep extensions lower-case; :func:`layout_aware_parse` lowers
# the file extension before lookup.
_DISPATCH: dict[str, Callable[[Path], list[ParsedElement]]] = {
    ".csv": _dispatch_csv,
    ".md": _dispatch_markdown,
    ".markdown": _dispatch_markdown,
    ".py": _dispatch_code,
    ".eml": _dispatch_email,
    ".pdf": _dispatch_pdf,
    ".xlsx": _dispatch_xlsx,
    ".docx": _dispatch_docx,
}


def is_supported(ext: str) -> bool:
    """Return True when the (lower-cased) extension has a Phase 2b parser."""
    return ext.lower() in _DISPATCH


def supported_extensions() -> list[str]:
    """List of all extensions the dispatcher handles."""
    return list(_DISPATCH)


def layout_aware_parse(file_path: str | Path) -> tuple[str, list[dict[str, Any]]] | None:
    """Parse ``file_path`` via the layout-aware pipeline.

    Returns:
        ``(raw_text, chunks)`` on a successful parse where ``chunks`` is the
        already-dispatched list of ``{text, metadata}`` dicts ready for the
        ChromaDB / BM25 write path. ``raw_text`` is the file's literal
        content (used by the caller for content_hash + AI categorization).

        Returns ``None`` when the extension isn't claimed by any Phase 2b
        parser — the caller should fall through to the legacy
        :func:`app.parsers.parse_file` path.

    The function never raises on a parse failure: if the dispatch raises
    (corrupt file, encoding error, etc), the failure is logged and
    ``None`` is returned so the caller's legacy fallback can attempt the
    file. This keeps the wire-in defensive — flipping
    ``ENABLE_LAYOUT_AWARE_PARSING=true`` should never make a previously-
    ingestible file fail to ingest.
    """
    p = Path(file_path)
    if not p.exists():
        # Mirror app.parsers.parse_file behaviour — let the caller raise.
        return None

    parser = _DISPATCH.get(p.suffix.lower())
    if parser is None:
        return None

    try:
        elements = parser(p)
    except Exception as exc:  # noqa: BLE001 — fall back to legacy parser
        logger.warning(
            "layout_aware_parse failed for %s (%s) — falling back to legacy",
            p.name, exc,
        )
        return None

    if not elements:
        # Empty parse — fall back so the legacy flat-text parser at least
        # tries to extract something.
        return None

    chunks = chunk_elements(elements)
    if not chunks:
        return None

    raw_text = p.read_text(encoding="utf-8", errors="replace")
    logger.info(
        "layout_aware_parse file=%s ext=%s elements=%d chunks=%d",
        p.name, p.suffix.lower(), len(elements), len(chunks),
    )
    return raw_text, chunks
