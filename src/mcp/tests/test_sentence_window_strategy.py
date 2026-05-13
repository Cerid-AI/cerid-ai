# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the sentence-window chunker (PR 4).

Verifies the LlamaIndex / Anthropic contextual-retrieval pattern:
each chunk's embedded text is one sentence; metadata carries the ±N
surrounding sentences in ``window_text`` for later LLM consumption.
"""
from __future__ import annotations

from typing import Any

import pytest

from core.ingest.chunkers.sentence_window_strategy import (
    _build_window,
    narrative_sentence_window_strategy,
    split_into_sentences,
)

# ---------------------------------------------------------------------------
# split_into_sentences — regex boundary heuristic
# ---------------------------------------------------------------------------


def test_split_simple_paragraph() -> None:
    text = "Cerid runs locally. It uses RAG. Privacy comes first."
    assert split_into_sentences(text) == [
        "Cerid runs locally.",
        "It uses RAG.",
        "Privacy comes first.",
    ]


def test_split_handles_question_and_exclamation() -> None:
    text = "Is RAG slow? No. Try it! It works."
    assert split_into_sentences(text) == [
        "Is RAG slow?",
        "No.",
        "Try it!",
        "It works.",
    ]


def test_split_empty_input_returns_empty_list() -> None:
    assert split_into_sentences("") == []
    assert split_into_sentences("   ") == []


def test_split_preserves_single_sentence() -> None:
    assert split_into_sentences("Just one.") == ["Just one."]


def test_split_handles_quoted_following_sentence() -> None:
    text = 'He said hello. "Then she replied."'
    out = split_into_sentences(text)
    assert len(out) == 2
    assert out[0] == "He said hello."


# ---------------------------------------------------------------------------
# _build_window — slice bounds clamped at edges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "n, idx, window, expected",
    [
        (10, 5, 3, (2, 9)),
        (10, 0, 3, (0, 4)),   # left edge
        (10, 9, 3, (6, 10)),  # right edge
        (1, 0, 3, (0, 1)),    # single-sentence element
        (5, 2, 100, (0, 5)),  # window larger than corpus
    ],
)
def test_build_window_bounds(
    n: int, idx: int, window: int, expected: tuple[int, int]
) -> None:
    sentences = [f"S{i}." for i in range(n)]
    assert _build_window(sentences, idx, window) == expected


# ---------------------------------------------------------------------------
# narrative_sentence_window_strategy — end-to-end chunk shape
# ---------------------------------------------------------------------------


def _element(text: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "text": text,
        "element_type": "NarrativeText",
        "metadata": meta or {},
    }


def test_strategy_emits_one_chunk_per_sentence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.ingest.chunkers.sentence_window_strategy.config.SENTENCE_WINDOW_SIZE",
        2,
        raising=False,
    )
    el = _element("Alpha sentence. Beta sentence. Gamma sentence. Delta sentence.")
    chunks = narrative_sentence_window_strategy(el)
    assert len(chunks) == 4
    assert [c["text"] for c in chunks] == [
        "Alpha sentence.",
        "Beta sentence.",
        "Gamma sentence.",
        "Delta sentence.",
    ]


def test_window_text_includes_neighbors_inside_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "core.ingest.chunkers.sentence_window_strategy.config.SENTENCE_WINDOW_SIZE",
        1,
        raising=False,
    )
    el = _element("A. B. C. D. E.")
    chunks = narrative_sentence_window_strategy(el)
    # idx=2 (C) with window=1 should see "B. C. D."
    assert chunks[2]["text"] == "C."
    assert chunks[2]["metadata"]["window_text"] == "B. C. D."
    # Edge cases at the bounds
    assert chunks[0]["metadata"]["window_text"] == "A. B."
    assert chunks[-1]["metadata"]["window_text"] == "D. E."


def test_window_indices_recorded_for_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "core.ingest.chunkers.sentence_window_strategy.config.SENTENCE_WINDOW_SIZE",
        1,
        raising=False,
    )
    el = _element("One. Two. Three.")
    chunks = narrative_sentence_window_strategy(el)
    mids = chunks[1]["metadata"]
    assert mids["sentence_idx"] == 1
    assert mids["window_start_idx"] == 0
    assert mids["window_end_idx"] == 3


def test_short_element_collapses_to_single_chunk() -> None:
    el = _element("Just one sentence.")
    chunks = narrative_sentence_window_strategy(el)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "Just one sentence."
    assert chunks[0]["metadata"]["window_text"] == "Just one sentence."
    assert chunks[0]["metadata"]["sentence_idx"] == 0


def test_empty_element_returns_no_chunks() -> None:
    assert narrative_sentence_window_strategy(_element("   ")) == []


def test_original_metadata_preserved() -> None:
    el = _element(
        "Sentence one. Sentence two.",
        meta={"page_num": 5, "source_path": "/tmp/file.txt"},
    )
    chunks = narrative_sentence_window_strategy(el)
    for c in chunks:
        assert c["metadata"]["page_num"] == 5
        assert c["metadata"]["source_path"] == "/tmp/file.txt"
        assert c["metadata"]["element_type"] == "NarrativeText"
