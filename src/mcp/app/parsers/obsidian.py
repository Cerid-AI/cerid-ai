# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Obsidian vault importer — recursively parses a vault directory.

Extracts Markdown notes, YAML frontmatter, ``[[wiki-links]]``, and maps
the folder structure to cerid domains (top-level folder = domain).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai-companion.parsers.obsidian")

# [[target]] or [[target|alias]]
_WIKI_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def parse_obsidian_vault(vault_path: str | Path) -> list[dict[str, Any]]:
    """Recursively parse an Obsidian vault directory.

    Parameters
    ----------
    vault_path:
        Root directory of the Obsidian vault.

    Returns
    -------
    list[dict]:
        Each dict contains ``title``, ``content``, ``metadata``, ``source``,
        and ``links`` (list of wiki-link targets).
    """
    vault = Path(vault_path)
    if not vault.is_dir():
        raise NotADirectoryError(f"Obsidian vault not found: {vault}")

    notes: list[dict[str, Any]] = []

    for md_file in sorted(vault.rglob("*.md")):
        # Skip hidden files/directories (e.g., .obsidian/, .trash/)
        rel = md_file.relative_to(vault)
        if any(part.startswith(".") for part in rel.parts):
            continue

        try:
            text = md_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.warning("Skipping non-UTF-8 file: %s", md_file)
            continue

        title = md_file.stem
        frontmatter, content = _split_frontmatter(text)
        metadata = _parse_yaml_frontmatter(frontmatter) if frontmatter else {}
        links = _extract_wiki_links(text)

        # Map folder structure to cerid domains
        # Top-level folder = domain (e.g., "Code/Python/decorators.md" -> domain "Code")
        parts = rel.parts
        if len(parts) > 1:
            metadata["domain"] = parts[0]
            metadata["folder_path"] = str(rel.parent)
        else:
            metadata["domain"] = "root"

        metadata["vault_path"] = str(rel)

        # Use frontmatter title if present
        if "title" in metadata:
            title = str(metadata.pop("title"))

        notes.append({
            "title": title,
            "content": content,
            "metadata": metadata,
            "source": "obsidian",
            "links": links,
        })

    logger.info("Parsed %d notes from Obsidian vault: %s", len(notes), vault.name)
    return notes


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split YAML frontmatter from the rest of the Markdown content.

    Returns (frontmatter_raw, body). frontmatter_raw is *None* when absent.
    """
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if m:
        return m.group(1), text[m.end():]
    return None, text


def _extract_wiki_links(text: str) -> list[str]:
    """Extract all ``[[wiki-link]]`` targets from the text."""
    return list(dict.fromkeys(_WIKI_LINK_RE.findall(text)))  # deduplicate, preserve order


def _parse_yaml_frontmatter(raw: str) -> dict[str, Any]:
    """Parse simple YAML frontmatter without requiring PyYAML.

    Handles ``key: value`` pairs and simple lists (``- item``).
    """
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # List item continuation
        if stripped.startswith("- ") and current_key is not None and current_list is not None:
            current_list.append(stripped[2:].strip().strip('"').strip("'"))
            continue

        # Flush any pending list
        if current_key is not None and current_list is not None:
            result[current_key] = current_list
            current_key = None
            current_list = None

        if ":" not in stripped:
            continue

        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()

        # Empty value followed by list items
        if not value:
            current_key = key
            current_list = []
            continue

        # Inline list: [a, b, c]
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",") if v.strip()]
            result[key] = items
            continue

        # Scalar
        value = value.strip('"').strip("'")
        if value.lower() in ("true", "yes"):
            result[key] = True
        elif value.lower() in ("false", "no"):
            result[key] = False
        elif value.isdigit():
            result[key] = int(value)
        else:
            result[key] = value

    # Flush trailing list
    if current_key is not None and current_list is not None:
        result[current_key] = current_list

    return result
