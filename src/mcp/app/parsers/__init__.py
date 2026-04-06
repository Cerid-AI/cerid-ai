# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Extensible file parser registry — re-exports all parsers for backward compatibility.

Usage::

    from parsers import parse_file, PARSER_REGISTRY
    result = parse_file("/path/to/document.pdf")
"""

__all__ = [
    "PARSER_REGISTRY", "_MAX_TEXT_CHARS", "parse_file", "register_parser",
    "_strip_html_tags", "_strip_rtf",
    "parse_pdf", "parse_docx", "parse_xlsx",
    "parse_csv", "parse_html", "parse_text",
    "parse_eml", "parse_mbox",
    "parse_epub", "parse_rtf",
]

# Registry and orchestration
# Shared utilities
from app.parsers._utils import _strip_html_tags, _strip_rtf  # noqa: F401
from app.parsers.ebook import parse_epub, parse_rtf  # noqa: F401
from app.parsers.email import parse_eml, parse_mbox  # noqa: F401
from app.parsers.office import parse_docx, parse_xlsx  # noqa: F401

# Eagerly import all parser modules to trigger @register_parser decorators
from app.parsers.pdf import parse_pdf  # noqa: F401
from app.parsers.registry import (  # noqa: F401
    _MAX_TEXT_CHARS,
    PARSER_REGISTRY,
    parse_file,
    register_parser,
)
from app.parsers.structured import parse_csv, parse_html, parse_text  # noqa: F401
