# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the wikilink extractor (RAG Cycle C2.1 Phase A)."""

from __future__ import annotations

from core.ingest.wikilinks import WikilinkRef, extract_wikilinks

# ---------------------------------------------------------------------------
# Plain forms
# ---------------------------------------------------------------------------

def test_plain_wikilink():
    refs = extract_wikilinks("See [[Foo]] for details.")
    assert len(refs) == 1
    ref = refs[0]
    assert ref.target == "Foo"
    assert ref.alias == "Foo"  # defaults to target
    assert ref.heading == ""
    assert ref.is_embed is False


def test_aliased_wikilink():
    refs = extract_wikilinks("See [[Foo|the foo doc]].")
    assert len(refs) == 1
    assert refs[0].target == "Foo"
    assert refs[0].alias == "the foo doc"
    assert refs[0].heading == ""
    assert refs[0].is_embed is False


def test_heading_wikilink():
    refs = extract_wikilinks("Jump to [[Foo#Installation]].")
    assert len(refs) == 1
    assert refs[0].target == "Foo"
    assert refs[0].heading == "Installation"
    assert refs[0].alias == "Foo"
    assert refs[0].is_embed is False


def test_embed_wikilink():
    refs = extract_wikilinks("Here: ![[diagram.png]]")
    assert len(refs) == 1
    assert refs[0].target == "diagram.png"
    assert refs[0].is_embed is True


def test_combined_heading_and_alias():
    refs = extract_wikilinks("[[Foo#Section A|see section]]")
    assert len(refs) == 1
    assert refs[0].target == "Foo"
    assert refs[0].heading == "Section A"
    assert refs[0].alias == "see section"


# ---------------------------------------------------------------------------
# Code exclusion
# ---------------------------------------------------------------------------

def test_backtick_inline_excluded():
    """Inline `[[code]]` spans should NOT yield a wikilink."""
    refs = extract_wikilinks("Use `[[code]]` in literals.")
    assert refs == []


def test_fenced_block_excluded_triple_backtick():
    md = (
        "Some text [[OutsideRef]] here.\n"
        "```\n"
        "[[InsideRef]]\n"
        "```\n"
        "More text.\n"
    )
    refs = extract_wikilinks(md)
    targets = [r.target for r in refs]
    assert "OutsideRef" in targets
    assert "InsideRef" not in targets


def test_fenced_block_excluded_tilde():
    md = (
        "Outside [[A]]\n"
        "~~~\n"
        "[[Inside]]\n"
        "~~~\n"
    )
    refs = extract_wikilinks(md)
    targets = [r.target for r in refs]
    assert "A" in targets
    assert "Inside" not in targets


def test_nested_brackets_only_inner_match():
    """``[[a [[b]] c]]`` should produce only the inner ``b`` match.

    The outer ``[[`` is left dangling because its target group
    contains characters the regex disallows.  Real notes don't have
    this pattern; the test pins down the behaviour so future regex
    tweaks don't accidentally start matching arbitrary nested brackets.
    """
    refs = extract_wikilinks("[[a [[b]] c]]")
    targets = [r.target for r in refs]
    assert "b" in targets
    # The outer "a " or "a [[" forms are not valid wikilinks
    assert "a" not in targets
    assert "a [[b" not in targets


# ---------------------------------------------------------------------------
# Dedup, edge cases
# ---------------------------------------------------------------------------

def test_duplicate_dedup():
    md = "Link [[Foo]] again [[Foo]] and once more [[Foo]]."
    refs = extract_wikilinks(md)
    assert len(refs) == 1
    assert refs[0].target == "Foo"


def test_dedup_distinct_heading_or_alias_kept_separate():
    """A target with different headings/aliases is NOT deduplicated."""
    md = "[[Foo]] and [[Foo#A]] and [[Foo|alias]]"
    refs = extract_wikilinks(md)
    keys = {(r.target, r.heading, r.alias, r.is_embed) for r in refs}
    assert len(keys) == 3


def test_empty_string():
    assert extract_wikilinks("") == []


def test_no_wikilinks_in_plain_text():
    assert extract_wikilinks("just regular prose with [single] brackets") == []


def test_returns_wikilink_ref_dataclass_instances():
    refs = extract_wikilinks("[[Foo]]")
    assert isinstance(refs[0], WikilinkRef)


# ---------------------------------------------------------------------------
# Input cap (defensive ReDoS bound)
# ---------------------------------------------------------------------------

def test_input_cap_at_50kb():
    """Inputs ≥ 50 KB return [] immediately."""
    big = "[[Foo]]\n" + ("x" * (51 * 1024))
    assert extract_wikilinks(big) == []


def test_just_under_cap_still_parses():
    """Input just under the 50 KB cap still parses normally."""
    # 49 KB of filler + a known wikilink
    padding = "x" * (49 * 1024)
    text = f"{padding}\n[[Foo]]\n"
    refs = extract_wikilinks(text)
    assert any(r.target == "Foo" for r in refs)
