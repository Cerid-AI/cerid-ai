# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Notion export importer — parses Notion HTML/Markdown ZIP exports.

Notion's "Export all as HTML" or "Export all as Markdown" feature produces a
ZIP archive where folders represent sub-pages and files contain page content.
This module extracts pages, preserves the hierarchy, and returns cerid-compatible
document dicts ready for ingestion.
"""

from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

logger = logging.getLogger("ai-companion.parsers.notion")


def parse_notion_export(zip_path: str | Path) -> list[dict[str, Any]]:
    """Extract pages from a Notion HTML/Markdown export ZIP.

    Parameters
    ----------
    zip_path:
        Path to the ZIP file exported from Notion.

    Returns
    -------
    list[dict]:
        Each dict contains ``title``, ``content``, ``metadata``, ``source``,
        and ``parent_id``.
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise FileNotFoundError(f"Notion export not found: {zip_path}")

    pages: list[dict[str, Any]] = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        # Build parent mapping: folder path -> page title derived from folder name
        folder_ids: dict[str, str] = {}

        for info in zf.infolist():
            if info.is_dir():
                continue

            name = info.filename
            pure = PurePosixPath(name)
            suffix = pure.suffix.lower()

            if suffix not in (".html", ".htm", ".md", ".markdown"):
                continue

            raw = zf.read(name)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1", errors="replace")

            title = _extract_title(pure.stem, text, suffix)
            content = _extract_content(text, suffix)
            metadata = _extract_metadata(text, suffix)

            # Determine parent from folder structure
            parent_folder = str(pure.parent)
            parent_id = folder_ids.get(parent_folder)

            # Register this page's folder for child lookups
            page_folder = str(pure.with_suffix(""))
            page_id = _notion_id_from_name(pure.stem) or ""
            folder_ids[page_folder] = page_id

            # Map Notion database properties to cerid metadata
            metadata["notion_path"] = name
            if page_id:
                metadata["notion_id"] = page_id

            pages.append({
                "title": title,
                "content": content,
                "metadata": metadata,
                "source": "notion",
                "parent_id": parent_id,
            })

    logger.info("Parsed %d pages from Notion export: %s", len(pages), zip_path.name)
    return pages


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Notion appends a 32-char hex ID to file/folder names: "Page Name abc123def456..."
_NOTION_ID_RE = re.compile(r"\s+([0-9a-f]{32})$")


def _notion_id_from_name(stem: str) -> str | None:
    """Extract the 32-char Notion block ID from a filename stem."""
    m = _NOTION_ID_RE.search(stem)
    return m.group(1) if m else None


def _clean_title(stem: str) -> str:
    """Strip the Notion ID suffix and clean up the title."""
    return _NOTION_ID_RE.sub("", stem).strip()


def _extract_title(stem: str, text: str, suffix: str) -> str:
    """Extract page title from content or fall back to filename."""
    if suffix in (".html", ".htm"):
        # Try <title> tag
        m = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        if m and m.group(1).strip():
            return m.group(1).strip()
        # Try first <h1>
        m = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.IGNORECASE | re.DOTALL)
        if m and m.group(1).strip():
            return re.sub(r"<[^>]+>", "", m.group(1)).strip()
    elif suffix in (".md", ".markdown"):
        # Try first # heading
        m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        if m and m.group(1).strip():
            return m.group(1).strip()

    return _clean_title(stem)


def _extract_content(text: str, suffix: str) -> str:
    """Extract the main content from a page."""
    if suffix in (".html", ".htm"):
        return _strip_html(text)
    # Markdown: strip frontmatter
    return _strip_frontmatter(text)


def _extract_metadata(text: str, suffix: str) -> dict[str, Any]:
    """Extract metadata from HTML headers or Markdown frontmatter."""
    metadata: dict[str, Any] = {}

    if suffix in (".html", ".htm"):
        # Extract from <header> or <meta> tags
        for m in re.finditer(
            r'<meta\s+(?:name|property)="([^"]+)"\s+content="([^"]*)"',
            text,
            re.IGNORECASE,
        ):
            metadata[m.group(1)] = m.group(2)
    elif suffix in (".md", ".markdown"):
        # Parse YAML frontmatter
        fm = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        if fm:
            metadata = _parse_yaml_frontmatter(fm.group(1))

    return metadata


def _strip_html(html: str) -> str:
    """Crude HTML to text — strips tags and collapses whitespace."""
    # Remove script/style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common entities
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " "), ("&quot;", '"')]:
        text = text.replace(entity, char)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter from Markdown content."""
    return re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.DOTALL)


def _parse_yaml_frontmatter(raw: str) -> dict[str, Any]:
    """Parse simple YAML frontmatter without requiring PyYAML.

    Handles ``key: value`` pairs only (no nested structures).
    """
    result: dict[str, Any] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if value.lower() in ("true", "yes"):
            result[key] = True
        elif value.lower() in ("false", "no"):
            result[key] = False
        elif value.isdigit():
            result[key] = int(value)
        else:
            result[key] = value
    return result
