# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the markdown wikilink-edge chunker strategy
(RAG Cycle C2.1 Phase B).
"""

from __future__ import annotations

from core.ingest.chunkers.markdown_strategy import (
    markdown_section_strategy,
    markdown_wikilink_edge_strategy,
)


def _section(text: str, heading_path: list[str] | None = None) -> dict:
    return {
        "text": text,
        "element_type": "MarkdownSection",
        "metadata": {
            "heading_path": heading_path or [],
            "level": len(heading_path or []),
            "headers": {},
        },
    }


# ---------------------------------------------------------------------------
# markdown_wikilink_edge_strategy
# ---------------------------------------------------------------------------

def test_emits_one_edge_per_unique_wikilink():
    element = _section("See [[Foo]] and [[Bar]] for context.")
    edges = markdown_wikilink_edge_strategy(element)
    assert len(edges) == 2
    targets = {e["metadata"]["wikilink_target"] for e in edges}
    assert targets == {"Foo", "Bar"}


def test_edge_chunk_has_empty_text():
    element = _section("Hi [[Foo]].")
    edges = markdown_wikilink_edge_strategy(element)
    assert all(e["text"] == "" for e in edges)


def test_edge_metadata_shape():
    element = _section("![[image.png]] and [[note#H|alias]]")
    edges = markdown_wikilink_edge_strategy(element)
    assert len(edges) == 2

    by_target = {e["metadata"]["wikilink_target"]: e["metadata"] for e in edges}
    embed_meta = by_target["image.png"]
    assert embed_meta["element_type"] == "WikilinkEdge"
    assert embed_meta["wikilink_is_embed"] == "true"
    assert embed_meta["wikilink_alias"] == "image.png"
    assert embed_meta["wikilink_heading"] == ""
    assert embed_meta["wikilink_source_chunk_idx"] == "0"

    link_meta = by_target["note"]
    assert link_meta["wikilink_is_embed"] == "false"
    assert link_meta["wikilink_alias"] == "alias"
    assert link_meta["wikilink_heading"] == "H"


def test_empty_body_returns_empty():
    element = _section("")
    assert markdown_wikilink_edge_strategy(element) == []


def test_no_wikilinks_returns_empty():
    element = _section("Just prose, no links here.")
    assert markdown_wikilink_edge_strategy(element) == []


def test_is_embed_string_form_only():
    """ChromaDB-compat string form (not bool) for is_embed."""
    element = _section("![[a.png]] [[b]]")
    edges = markdown_wikilink_edge_strategy(element)
    for e in edges:
        v = e["metadata"]["wikilink_is_embed"]
        assert isinstance(v, str)
        assert v in {"true", "false"}


# ---------------------------------------------------------------------------
# markdown_section_strategy composition (non-regression + new behavior)
# ---------------------------------------------------------------------------

def test_section_strategy_emits_text_plus_edges():
    """Body with two wikilinks → one text chunk + two WikilinkEdge chunks."""
    element = _section(
        "See [[Foo]] and [[Bar]] for context.",
        heading_path=["Top"],
    )
    chunks = markdown_section_strategy(element)
    text_chunks = [c for c in chunks if c["metadata"]["element_type"] == "MarkdownSection"]
    edge_chunks = [c for c in chunks if c["metadata"]["element_type"] == "WikilinkEdge"]
    assert len(text_chunks) == 1
    assert len(edge_chunks) == 2


def test_section_strategy_no_links_is_pure_text():
    """A section with no wikilinks emits only the text chunk."""
    element = _section("Just prose.", heading_path=["H1"])
    chunks = markdown_section_strategy(element)
    assert len(chunks) == 1
    assert chunks[0]["metadata"]["element_type"] == "MarkdownSection"


def test_section_strategy_preserves_breadcrumb():
    """Non-regression: text chunks still carry the heading breadcrumb."""
    element = _section(
        "Body text [[Linked]].",
        heading_path=["Top", "Sub"],
    )
    chunks = markdown_section_strategy(element)
    text_chunk = next(c for c in chunks if c["metadata"]["element_type"] == "MarkdownSection")
    assert text_chunk["text"].startswith("Top > Sub")
    assert "Body text" in text_chunk["text"]


def test_section_strategy_oversized_body_still_emits_edges(monkeypatch):
    """When the body splits across multiple text chunks, the wikilink
    edges should still be emitted (the strategy doesn't lose them in
    the split-path branch)."""
    import config

    monkeypatch.setattr(config, "PARENT_CHUNK_TOKENS", 50)
    element = _section(
        "[[Foo]] " + ("filler word " * 200) + "[[Bar]]",
        heading_path=["Title"],
    )
    chunks = markdown_section_strategy(element)
    edge_chunks = [c for c in chunks if c["metadata"]["element_type"] == "WikilinkEdge"]
    targets = {c["metadata"]["wikilink_target"] for c in edge_chunks}
    assert targets == {"Foo", "Bar"}


def test_section_strategy_ignores_wikilinks_in_code_fences():
    """Fenced code blocks should not produce edge chunks."""
    body = (
        "Real link: [[Real]]\n"
        "```\n"
        "[[CodeFence]]\n"
        "```\n"
    )
    element = _section(body, heading_path=["H"])
    chunks = markdown_section_strategy(element)
    edge_targets = {
        c["metadata"]["wikilink_target"]
        for c in chunks
        if c["metadata"]["element_type"] == "WikilinkEdge"
    }
    assert "Real" in edge_targets
    assert "CodeFence" not in edge_targets
