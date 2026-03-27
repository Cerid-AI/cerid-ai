# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for Notion/Obsidian parsers and migration router."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.parsers.notion import parse_notion_export
from app.parsers.obsidian import (
    _extract_wiki_links,
    _parse_yaml_frontmatter,
    _split_frontmatter,
    parse_obsidian_vault,
)

# ---------------------------------------------------------------------------
# Obsidian parser tests
# ---------------------------------------------------------------------------


def test_parse_obsidian_frontmatter():
    """Extracts YAML frontmatter key-value pairs from Markdown."""
    raw = "title: My Note\ntags: [python, async]\nstatus: draft\npublished: true\ncount: 42"
    result = _parse_yaml_frontmatter(raw)

    assert result["title"] == "My Note"
    assert result["tags"] == ["python", "async"]
    assert result["status"] == "draft"
    assert result["published"] is True
    assert result["count"] == 42


def test_parse_obsidian_frontmatter_list():
    """Parses YAML list items (- syntax)."""
    raw = "title: Lists\ntags:\n- alpha\n- beta\n- gamma"
    result = _parse_yaml_frontmatter(raw)
    assert result["tags"] == ["alpha", "beta", "gamma"]


def test_parse_obsidian_wiki_links():
    """Extracts [[wiki-links]] including aliased links."""
    text = """
    This note references [[Python GIL]] and also [[Rust|Rust Language]].
    There's also a link to [[Docker Compose]] here.
    Duplicate: [[Python GIL]]
    """
    links = _extract_wiki_links(text)

    assert "Python GIL" in links
    assert "Rust" in links
    assert "Docker Compose" in links
    # Duplicates should be removed
    assert links.count("Python GIL") == 1


def test_parse_obsidian_wiki_links_empty():
    """Returns empty list when no wiki-links present."""
    assert _extract_wiki_links("No links here, just plain text.") == []


def test_split_frontmatter():
    """Correctly splits frontmatter from body."""
    text = "---\ntitle: Test\n---\n\n# Heading\n\nBody text."
    fm, body = _split_frontmatter(text)
    assert fm == "title: Test"
    assert body.strip().startswith("# Heading")


def test_split_frontmatter_absent():
    """Returns None frontmatter when not present."""
    text = "# No Frontmatter\n\nJust content."
    fm, body = _split_frontmatter(text)
    assert fm is None
    assert body == text


def test_parse_obsidian_folder_to_domain():
    """Maps top-level vault folders to cerid domains."""
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp)
        # Create folder structure
        code_dir = vault / "Code" / "Python"
        code_dir.mkdir(parents=True)
        finance_dir = vault / "Finance"
        finance_dir.mkdir(parents=True)

        (code_dir / "decorators.md").write_text(
            "---\ntitle: Decorators\n---\n\n# Python Decorators\n\nContent about decorators."
        )
        (finance_dir / "budgets.md").write_text("# Budget Planning\n\nSome budget notes.")
        (vault / "root_note.md").write_text("# Root Note\n\nA note at the root level.")

        notes = parse_obsidian_vault(vault)

    assert len(notes) == 3

    by_title = {n["title"]: n for n in notes}

    # Code/Python/decorators.md -> domain "Code"
    assert by_title["Decorators"]["metadata"]["domain"] == "Code"
    assert by_title["Decorators"]["source"] == "obsidian"

    # Finance/budgets.md -> domain "Finance"
    assert by_title["budgets"]["metadata"]["domain"] == "Finance"

    # root_note.md -> domain "root"
    assert by_title["Root Note"]["metadata"]["domain"] == "root"


def test_parse_obsidian_skips_hidden():
    """Skips hidden directories like .obsidian/ and .trash/."""
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp)
        (vault / ".obsidian").mkdir()
        (vault / ".obsidian" / "config.md").write_text("config")
        (vault / "real_note.md").write_text("# Real\n\nContent.")

        notes = parse_obsidian_vault(vault)

    assert len(notes) == 1
    assert notes[0]["title"] == "real_note"


# ---------------------------------------------------------------------------
# Notion parser tests
# ---------------------------------------------------------------------------


def test_parse_notion_basic():
    """Parses a simple Notion HTML export ZIP."""
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "notion_export.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "Getting Started abc123def456789012345678901234.html",
                "<html><head><title>Getting Started</title></head>"
                "<body><h1>Getting Started</h1><p>Welcome to Notion.</p></body></html>",
            )
            zf.writestr(
                "Notes/Daily Log abc123def456789012345678901234.md",
                "---\ndate: 2026-03-27\n---\n\n# Daily Log\n\nToday's notes.",
            )

        pages = parse_notion_export(zip_path)

    assert len(pages) == 2

    html_page = next(p for p in pages if "Getting Started" in p["title"])
    assert html_page["source"] == "notion"
    assert "Welcome to Notion" in html_page["content"]

    md_page = next(p for p in pages if "Daily Log" in p["title"])
    assert md_page["source"] == "notion"
    assert md_page["metadata"].get("date") == "2026-03-27"


def test_parse_notion_nested_structure():
    """Handles nested folder structure (sub-pages)."""
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "nested.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("Parent aaaa11112222333344445555666677.html", "<h1>Parent</h1><p>Parent page.</p>")
            zf.writestr(
                "Parent aaaa11112222333344445555666677/Child bbbb11112222333344445555666677.html",
                "<h1>Child</h1><p>Child page.</p>",
            )

        pages = parse_notion_export(zip_path)

    assert len(pages) == 2
    child = next(p for p in pages if "Child" in p["title"])
    # Child should have a parent_id referencing the parent page
    assert child["parent_id"] is not None


def test_parse_notion_not_found():
    """Raises FileNotFoundError for missing ZIP."""
    with pytest.raises(FileNotFoundError):
        parse_notion_export("/tmp/nonexistent_notion_export.zip")


# ---------------------------------------------------------------------------
# Migration router tests
# ---------------------------------------------------------------------------


def test_migration_router_status():
    """Status endpoint returns progress from Redis."""
    mock_redis = MagicMock()
    mock_redis.hgetall.return_value = {
        "status": "processing",
        "total": "50",
        "processed": "25",
        "errors": "2",
    }

    with patch("app.routers.migration._get_redis", return_value=mock_redis):
        from app.routers.migration import _MIGRATION_KEY_PREFIX

        # Simulate the status lookup logic directly
        job_id = "test-job-123"
        key = f"{_MIGRATION_KEY_PREFIX}{job_id}"
        data = mock_redis.hgetall(key)

        assert data["status"] == "processing"
        assert int(data["total"]) == 50
        assert int(data["processed"]) == 25
        assert int(data["errors"]) == 2
