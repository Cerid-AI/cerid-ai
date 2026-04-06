# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Parser registry — @register_parser decorator maps extensions to parsers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai-companion.parsers")

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
PARSER_REGISTRY: dict[str, Callable[[str], dict[str, Any]]] = {}


def register_parser(extensions: list[str]):
    """Decorator that maps file extensions to a parser function."""
    def decorator(func: Callable[[str], dict[str, Any]]):
        for ext in extensions:
            PARSER_REGISTRY[ext.lower()] = func
        return func
    return decorator


def parse_file(file_path: str) -> dict[str, Any]:
    """Parse a file and return {"text", "file_type", "page_count"}."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    if path.stat().st_size == 0:
        raise ValueError(f"File is empty (0 bytes): {path.name}")

    ext = path.suffix.lower()
    parser = PARSER_REGISTRY.get(ext)
    if not parser:
        raise ValueError(
            f"Unsupported file type: {ext}. "
            f"Supported: {sorted(PARSER_REGISTRY.keys())}"
        )
    return parser(file_path)


# Maximum text output size to prevent memory issues with huge files
_MAX_TEXT_CHARS = 2_000_000  # ~2MB of text
