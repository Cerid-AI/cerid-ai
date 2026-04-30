# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Markdown header-hierarchy parser + strategy
(Workstream E Phase 2b.2).
"""

from __future__ import annotations

import pytest

from core.ingest.chunkers import chunk_elements
from core.ingest.chunkers.markdown_strategy import markdown_section_strategy
from core.ingest.parsers.markdown_header import (
    parse_markdown,
    parse_markdown_string,
)

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_markdown_emits_section_per_leaf_heading():
    md = (
        "# Top\nIntro paragraph.\n\n"
        "## Sub A\nSub A body.\n\n"
        "## Sub B\nSub B body.\n"
    )
    elements = parse_markdown_string(md)
    assert len(elements) == 3
    for el in elements:
        assert el["element_type"] == "MarkdownSection"


def test_parse_markdown_heading_path_is_ordered_h1_to_deepest():
    md = (
        "# Top\n"
        "## Sub\n"
        "### Leaf\n"
        "Leaf body.\n"
    )
    elements = parse_markdown_string(md)
    leaf = next(el for el in elements if "Leaf body" in el["text"])
    assert leaf["metadata"]["heading_path"] == ["Top", "Sub", "Leaf"]
    assert leaf["metadata"]["level"] == 3


def test_parse_markdown_preserves_raw_headers_dict():
    md = "# Foo\n## Bar\nbody\n"
    elements = parse_markdown_string(md)
    el = next(el for el in elements if el["metadata"]["heading_path"])
    assert el["metadata"]["headers"] == {"h1": "Foo", "h2": "Bar"}


def test_parse_markdown_skips_empty_sections():
    """Headings without body text don't generate sections."""
    md = "# Empty\n# Another\n## Sub\nactual body\n"
    elements = parse_markdown_string(md)
    assert all("actual body" in el["text"] or el["text"].strip() for el in elements)


def test_parse_markdown_empty_string_returns_empty():
    assert parse_markdown_string("") == []
    assert parse_markdown_string("   \n  \n") == []


def test_parse_markdown_handles_h4_to_h6():
    md = (
        "# H1\n## H2\n### H3\n#### H4\n##### H5\n###### H6\n"
        "Deep body.\n"
    )
    elements = parse_markdown_string(md)
    leaf = next(el for el in elements if "Deep body" in el["text"])
    assert leaf["metadata"]["level"] == 6
    assert len(leaf["metadata"]["heading_path"]) == 6


def test_parse_markdown_file_round_trip(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text("# Title\n## Section\nBody.\n", encoding="utf-8")
    elements = parse_markdown(p)
    assert len(elements) == 1
    assert elements[0]["metadata"]["heading_path"] == ["Title", "Section"]


def test_parse_markdown_raises_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_markdown(tmp_path / "missing.md")


# ---------------------------------------------------------------------------
# Strategy: heading-breadcrumb prepend
# ---------------------------------------------------------------------------


def test_markdown_strategy_prepends_breadcrumb():
    element = {
        "text": "the body of the section",
        "element_type": "MarkdownSection",
        "metadata": {
            "heading_path": ["Top", "Sub", "Leaf"],
            "level": 3,
            "headers": {"h1": "Top", "h2": "Sub", "h3": "Leaf"},
        },
    }
    chunks = markdown_section_strategy(element)
    assert len(chunks) == 1
    text = chunks[0]["text"]
    # Breadcrumb format: "Top > Sub > Leaf\n\n<body>"
    assert text.startswith("Top > Sub > Leaf")
    assert "the body of the section" in text


def test_markdown_strategy_metadata_preserved():
    element = {
        "text": "body",
        "element_type": "MarkdownSection",
        "metadata": {"heading_path": ["A"], "level": 1, "headers": {"h1": "A"}},
    }
    chunks = markdown_section_strategy(element)
    md = chunks[0]["metadata"]
    assert md["element_type"] == "MarkdownSection"
    assert md["heading_path"] == ["A"]
    assert md["level"] == 1


def test_markdown_strategy_handles_missing_heading_path():
    """A section without a heading_path (e.g. preamble before any heading)
    still produces a usable chunk — just without the breadcrumb."""
    element = {
        "text": "prelude paragraph",
        "element_type": "MarkdownSection",
        "metadata": {},
    }
    chunks = markdown_section_strategy(element)
    assert chunks[0]["text"] == "prelude paragraph"


def test_markdown_strategy_splits_oversized_body(monkeypatch):
    """When the body exceeds PARENT_CHUNK_TOKENS, split + re-prepend
    breadcrumb on each piece."""
    import config

    monkeypatch.setattr(config, "PARENT_CHUNK_TOKENS", 50)
    element = {
        "text": ("paragraph sentence " * 60),  # ~120 tokens
        "element_type": "MarkdownSection",
        "metadata": {
            "heading_path": ["Title"],
            "level": 1,
            "headers": {"h1": "Title"},
        },
    }
    chunks = markdown_section_strategy(element)
    assert len(chunks) >= 2
    for c in chunks:
        # Breadcrumb sticks to every sub-chunk so retrieval keeps the anchor
        assert c["text"].startswith("Title")
        assert c["metadata"]["heading_path"] == ["Title"]
    # section_chunk_idx is monotonically incrementing
    indices = [c["metadata"].get("section_chunk_idx") for c in chunks]
    assert indices == list(range(len(chunks)))


# ---------------------------------------------------------------------------
# End-to-end dispatch
# ---------------------------------------------------------------------------


def test_chunk_elements_dispatches_markdown_strategy():
    """parse → chunk_elements pipes through the registered strategy
    (verified by the breadcrumb showing up in chunk text)."""
    md = "# Top\n## Sub\nLeaf body.\n"
    elements = parse_markdown_string(md)
    chunks = chunk_elements(elements)
    assert len(chunks) == 1
    assert chunks[0]["text"].startswith("Top > Sub")
    assert "Leaf body" in chunks[0]["text"]
    assert chunks[0]["metadata"]["heading_path"] == ["Top", "Sub"]
