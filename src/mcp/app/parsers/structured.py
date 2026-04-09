# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Structured data and text parsers — CSV/TSV, HTML, plain text."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.parsers.registry import _MAX_TEXT_CHARS, logger, register_parser


@register_parser([".csv", ".tsv"])
def parse_csv(file_path: str) -> dict[str, Any]:
    """Parse CSV/TSV with auto-delimiter detection and schema summary."""
    import csv as csv_module

    import pandas as pd

    fname = Path(file_path).name
    ext = Path(file_path).suffix.lower()

    delimiter = "\t" if ext == ".tsv" else ","
    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            sample = f.read(8192)
        try:
            dialect = csv_module.Sniffer().sniff(sample, delimiters=",;\t|")
            delimiter = dialect.delimiter
        except csv_module.Error:
            pass  # keep default
    except Exception:
        pass

    try:
        try:
            df = pd.read_csv(file_path, encoding="utf-8", sep=delimiter)
        except UnicodeDecodeError:
            df = pd.read_csv(file_path, encoding="latin-1", sep=delimiter)
    except Exception as e:
        raise ValueError(
            f"Failed to read CSV '{fname}': {e}. "
            f"File may be corrupted or not a valid CSV."
        ) from e

    row_count = len(df)
    columns = list(df.columns)
    truncated = False

    if row_count > 5000:
        logger.warning(
            f"CSV '{fname}' has {row_count} rows, truncating to first 5000 for ingestion"
        )
        df = df.head(5000)
        truncated = True

    type_map = {}
    for col in columns:
        dtype = str(df[col].dtype)
        if "int" in dtype:
            type_map[col] = "integer"
        elif "float" in dtype:
            type_map[col] = "number"
        elif "datetime" in dtype:
            type_map[col] = "datetime"
        elif "bool" in dtype:
            type_map[col] = "boolean"
        else:
            type_map[col] = "text"

    schema_lines = [f"Schema: {len(columns)} columns, {row_count} rows"]
    schema_lines.append("Columns: " + ", ".join(f"{c} ({type_map.get(c, 'text')})" for c in columns[:30]))
    if len(columns) > 30:
        schema_lines.append(f"  ... and {len(columns) - 30} more columns")

    sample_df = df.head(5)
    sample_text = sample_df.to_string(index=False)

    full_text = df.to_string(index=False)

    text = "\n".join(schema_lines) + "\n\n--- Sample (first 5 rows) ---\n" + sample_text + "\n\n--- Full Data ---\n" + full_text

    result: dict[str, Any] = {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": ext.lstrip("."),
        "page_count": None,
        "row_count": row_count,
        "columns": json.dumps(columns[:50]),
        "schema": json.dumps(type_map),
    }
    if truncated:
        result["truncated"] = True

    return result


@register_parser([".html", ".htm"])
def parse_html(file_path: str) -> dict[str, Any]:
    """Parse HTML files, stripping tags to extract readable text."""
    path = Path(file_path)
    raw = path.read_text(encoding="utf-8", errors="replace")

    try:
        from html.parser import HTMLParser

        class _TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self._parts: list[str] = []
                self._skip = False

            def handle_starttag(self, tag, attrs):
                self._skip = tag.lower() in ("script", "style", "noscript")

            def handle_endtag(self, tag):
                if tag.lower() in ("script", "style", "noscript"):
                    self._skip = False

            def handle_data(self, data):
                if not self._skip:
                    stripped = data.strip()
                    if stripped:
                        self._parts.append(stripped)

        extractor = _TextExtractor()
        extractor.feed(raw)
        text = "\n".join(extractor._parts)
    except Exception:
        # Fallback: return raw if parsing fails
        text = raw

    return {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": "html",
        "page_count": None,
    }


@register_parser([
    ".txt", ".md", ".rst", ".log",
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".sh", ".bash",
    ".xml",
    ".java", ".go", ".rs", ".rb", ".cpp", ".c", ".h", ".cs",
    ".sql", ".r", ".swift", ".kt",
])
def parse_text(file_path: str) -> dict[str, Any]:
    path = Path(file_path)

    try:
        with open(file_path, "rb") as f:
            sample = f.read(512)
        if b"\x00" in sample:
            raise ValueError(
                f"File '{path.name}' appears to be a binary file "
                f"(null bytes detected). Only text files are supported."
            )
    except ValueError:
        raise
    except Exception:
        pass  # proceed with text read if binary check fails

    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": path.suffix.lstrip("."),
        "page_count": None,
    }
