# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the parser-protocol scaffold (Workstream E Phase 2a).

The Phase 2a contract is library-agnostic — these tests prove the
shape is honoured regardless of which format library Phase 2b lands.
"""

from __future__ import annotations

from core.ingest.chunkers import chunk_elements, register
from core.ingest.parsers import (
    PARSER_VERSION,
    ParsedElement,
    is_legacy_return,
    is_parsed_elements,
)


def test_parser_version_is_a_positive_int():
    """The version flag is the discriminator the registry shim checks."""
    assert isinstance(PARSER_VERSION, int)
    assert PARSER_VERSION >= 2


def test_is_parsed_elements_accepts_well_formed_list():
    elements: list[ParsedElement] = [
        {"text": "Hello world", "element_type": "Title"},
        {"text": "Body para", "element_type": "NarrativeText",
         "metadata": {"page_num": 1}},
    ]
    assert is_parsed_elements(elements) is True


def test_is_parsed_elements_accepts_empty_list():
    """An empty parse is a valid result (e.g. an empty PDF)."""
    assert is_parsed_elements([]) is True


def test_is_parsed_elements_rejects_dict_legacy_shape():
    """Pre-Phase-2 parsers returned dicts — dispatcher must distinguish."""
    legacy = {"text": "...", "file_type": "pdf", "page_count": 3}
    assert is_parsed_elements(legacy) is False


def test_is_parsed_elements_rejects_tuple_legacy_shape():
    """Some pre-Phase-2 parsers returned ``(text, file_type, page_count)``."""
    legacy = ("text", "pdf", 3)
    assert is_parsed_elements(legacy) is False


def test_is_parsed_elements_rejects_string():
    """Defensive: bare string is sometimes returned by the simplest parsers."""
    assert is_parsed_elements("just some text") is False


def test_is_legacy_return_inverts_is_parsed_elements_for_lists():
    """For list-shaped values, the two helpers are inverses."""
    elements: list[ParsedElement] = [{"text": "x", "element_type": "Title"}]
    assert is_legacy_return(elements) is False
    assert is_legacy_return({"file_type": "pdf"}) is True
    assert is_legacy_return("plain text") is True


# ---------------------------------------------------------------------------
# Chunker registry
# ---------------------------------------------------------------------------


def test_chunk_elements_falls_back_to_token_chunker():
    """No strategy registered → token chunker, metadata preserved."""
    elements: list[ParsedElement] = [
        {
            "text": "Body text long enough to chunk.",
            "element_type": "NarrativeText",
            "metadata": {"page_num": 4},
        },
    ]
    chunks = chunk_elements(elements)
    assert len(chunks) >= 1
    for c in chunks:
        assert "text" in c
        assert c["metadata"]["element_type"] == "NarrativeText"
        assert c["metadata"]["page_num"] == 4


def test_register_and_dispatch_strategy():
    """Custom strategy receives the element verbatim and overrides default."""
    seen: list[ParsedElement] = []

    def upper_case(element: ParsedElement) -> list[dict]:
        seen.append(element)
        return [{"text": element["text"].upper(), "metadata": {}}]

    register("Title", upper_case)
    elements: list[ParsedElement] = [
        {"text": "hello", "element_type": "Title"},
    ]
    chunks = chunk_elements(elements)
    assert chunks == [{"text": "HELLO", "metadata": {}}]
    assert seen and seen[0]["text"] == "hello"


def test_chunk_elements_handles_mixed_types():
    """Title (custom strategy) + NarrativeText (fallback) coexist."""
    # The Title strategy is still registered from the prior test;
    # NarrativeText falls through to the token chunker.
    elements: list[ParsedElement] = [
        {"text": "page heading", "element_type": "Title"},
        {"text": "Body content here.", "element_type": "NarrativeText"},
    ]
    chunks = chunk_elements(elements)
    # First chunk came from the registered Title strategy
    assert chunks[0]["text"] == "PAGE HEADING"
    # Subsequent chunks from the fallback path carry NarrativeText metadata
    assert any(c["metadata"].get("element_type") == "NarrativeText" for c in chunks)
