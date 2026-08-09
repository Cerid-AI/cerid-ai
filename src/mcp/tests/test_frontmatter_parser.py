# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the YAML frontmatter extractor (RAG Cycle C2.2 Phase A)."""
from __future__ import annotations

import logging

import pytest

from core.ingest.frontmatter import (
    extract_frontmatter,
    is_allowlisted,
)

# ---------------------------------------------------------------------------
# is_allowlisted
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", [
    "tags",
    "aliases",
    "cssclass",
    "status",
    "created",
    "updated",
    "source",
])
def test_is_allowlisted_reserved(key):
    assert is_allowlisted(key) is True


@pytest.mark.parametrize("key", [
    "cerid:priority",
    "cerid:reviewed",
    "cerid:internal_id",
])
def test_is_allowlisted_cerid_prefix(key):
    assert is_allowlisted(key) is True


@pytest.mark.parametrize("key", [
    "title",
    "author",
    "description",
    "TAGS",  # case-sensitive: rejected
    "Aliases",  # case-sensitive: rejected
])
def test_is_allowlisted_rejects_unknown_keys(key):
    assert is_allowlisted(key) is False


# ---------------------------------------------------------------------------
# extract_frontmatter — empty/no-frontmatter cases
# ---------------------------------------------------------------------------

def test_empty_input():
    props, body = extract_frontmatter("")
    assert props == {}
    assert body == ""


def test_no_frontmatter_passthrough():
    text = "# Hello\n\nSome body text."
    props, body = extract_frontmatter(text)
    assert props == {}
    assert body == text


def test_no_leading_fence_does_not_strip():
    # Even if a ``---`` appears mid-document, only a leading fence
    # counts.  This protects against false-positives on horizontal-rule
    # syntax.
    text = "Some content\n\n---\nkey: value\n---\n\nMore content"
    props, body = extract_frontmatter(text)
    assert props == {}
    assert body == text


# ---------------------------------------------------------------------------
# extract_frontmatter — valid frontmatter
# ---------------------------------------------------------------------------

def test_basic_reserved_keys_extracted():
    text = (
        "---\n"
        "status: draft\n"
        "source: gist\n"
        "---\n"
        "# Heading\n"
    )
    props, body = extract_frontmatter(text)
    assert props == {"status": "draft", "source": "gist"}
    assert body == "# Heading\n"


def test_mixed_reserved_and_ignored_keys():
    text = (
        "---\n"
        "status: published\n"
        "title: Ignored Title\n"
        "author: Ignored Author\n"
        "source: web\n"
        "---\n"
        "Body."
    )
    props, body = extract_frontmatter(text)
    assert props == {"status": "published", "source": "web"}
    assert "title" not in props
    assert "author" not in props
    assert body == "Body."


def test_tags_list_extracted():
    text = (
        "---\n"
        "tags: [python, rag, retrieval]\n"
        "---\n"
        "Body"
    )
    props, body = extract_frontmatter(text)
    assert props == {"tags": ["python", "rag", "retrieval"]}


def test_tags_string_coerced_to_list():
    """Obsidian's single-string ``tags: foo`` form normalises to a list."""
    text = "---\ntags: solo\n---\n"
    props, _ = extract_frontmatter(text)
    assert props == {"tags": ["solo"]}


def test_aliases_string_coerced_to_list():
    text = "---\naliases: SoloAlias\n---\n"
    props, _ = extract_frontmatter(text)
    assert props == {"aliases": ["SoloAlias"]}


def test_aliases_list_passthrough():
    text = "---\naliases: [Foo, Bar, Baz]\n---\n"
    props, _ = extract_frontmatter(text)
    assert props == {"aliases": ["Foo", "Bar", "Baz"]}


def test_cerid_custom_keys_extracted():
    text = (
        "---\n"
        "cerid:priority: high\n"
        "cerid:reviewed: true\n"
        "ignored_key: dropped\n"
        "---\n"
    )
    props, _ = extract_frontmatter(text)
    assert props.get("cerid:priority") == "high"
    assert props.get("cerid:reviewed") is True
    assert "ignored_key" not in props


def test_created_and_updated_extracted():
    text = (
        "---\n"
        "created: 2024-01-15\n"
        "updated: 2026-05-10T08:30:00\n"
        "---\n"
    )
    props, _ = extract_frontmatter(text)
    # PyYAML decodes ISO dates to datetime.date / datetime.datetime
    # objects.  The service-layer timestamp coercer handles those at
    # the ingestion boundary; the parser preserves the parsed shape.
    assert "created" in props
    assert "updated" in props


def test_cssclass_extracted():
    text = "---\ncssclass: cards\n---\n"
    props, _ = extract_frontmatter(text)
    assert props == {"cssclass": "cards"}


# ---------------------------------------------------------------------------
# extract_frontmatter — malformed / edge cases
# ---------------------------------------------------------------------------

def test_missing_closing_fence_returns_original():
    text = "---\nstatus: orphan\n\n# Heading\n\nBody"
    props, body = extract_frontmatter(text)
    assert props == {}
    assert body == text


def test_malformed_yaml_returns_original(caplog):
    text = "---\n[: not valid yaml\n---\nBody"
    with caplog.at_level(logging.DEBUG, logger="ai-companion.ingest.frontmatter"):
        props, body = extract_frontmatter(text)
    assert props == {}
    assert body == text
    # Debug log should have fired without exception bubbling.
    assert any(
        "yaml_parse_failed" in rec.message or "frontmatter" in rec.name
        for rec in caplog.records
    )


def test_empty_frontmatter_block_yields_empty_props():
    text = "---\n---\nBody"
    props, body = extract_frontmatter(text)
    assert props == {}
    assert body == "Body"


def test_frontmatter_with_only_unknown_keys_drops_all():
    text = (
        "---\n"
        "title: My Note\n"
        "author: Someone\n"
        "category: Notes\n"
        "---\n"
        "Body"
    )
    props, body = extract_frontmatter(text)
    assert props == {}
    assert body == "Body"


def test_non_string_keys_dropped():
    """YAML can produce int keys (``1: foo``).  We only accept str keys."""
    text = (
        "---\n"
        "1: numeric_key_dropped\n"
        "status: kept\n"
        "---\n"
    )
    props, _ = extract_frontmatter(text)
    assert props == {"status": "kept"}


def test_scalar_frontmatter_returns_empty_props():
    """A frontmatter block that's a scalar (not a mapping) returns
    empty props but the fence block is still stripped from the body."""
    text = "---\njust_a_string\n---\nBody"
    props, body = extract_frontmatter(text)
    assert props == {}
    # The fence WAS recognised, so body is stripped.
    assert body == "Body"


def test_fence_with_trailing_whitespace_still_matched():
    text = "---   \nstatus: ok\n---  \nBody"
    props, body = extract_frontmatter(text)
    assert props == {"status": "ok"}
    assert body == "Body"


def test_multiline_value_in_frontmatter():
    text = (
        "---\n"
        "tags:\n"
        "  - python\n"
        "  - rag\n"
        "status: draft\n"
        "---\n"
        "Body"
    )
    props, body = extract_frontmatter(text)
    assert props == {"tags": ["python", "rag"], "status": "draft"}
    assert body == "Body"
