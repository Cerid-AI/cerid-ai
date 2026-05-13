# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sentence-window chunker for NarrativeText elements.

Implements the LlamaIndex / Anthropic contextual-retrieval pattern: each chunk's
embedded text is a single sentence (or small group), while a wider window of
±``SENTENCE_WINDOW_SIZE`` surrounding sentences is stashed in metadata under
``window_text``. At generation time, the larger window is what gets fed to
the LLM — decoupling retrieval precision (small chunks match better) from
generation context (wider windows answer better).

Activated by ``ENABLE_SENTENCE_WINDOW=true`` via the chunker registry. The
registration is conditional so the legacy fallback (`token chunker on
NarrativeText`) is preserved when the flag is off.

References:
- https://docs.llamaindex.ai/en/stable/examples/node_postprocessor/MetadataReplacementDemo/
- https://www.anthropic.com/news/contextual-retrieval
"""
from __future__ import annotations

import re
from typing import Any

import config
from core.ingest.parsers import ParsedElement

# Lightweight sentence splitter — matches an end-of-sentence punctuation mark
# followed by whitespace and an uppercase letter or a digit. Misses some edge
# cases (Mr. / e.g. / etc) but is good enough for v1; richer splitters would
# add a spaCy dependency we don't otherwise need.
_SENTENCE_BOUNDARY = re.compile(
    r"""
    (?<=[\.\?\!])   # end-of-sentence punctuation lookbehind
    \s+             # one or more whitespace
    (?=[A-Z0-9"“\(\[])  # next token starts with capital, digit, quote, or bracket
    """,
    re.VERBOSE,
)


def split_into_sentences(text: str) -> list[str]:
    """Split text into sentences. Empty input returns empty list."""
    text = text.strip()
    if not text:
        return []
    sentences = _SENTENCE_BOUNDARY.split(text)
    # Drop leading/trailing whitespace on each, filter empties.
    return [s.strip() for s in sentences if s.strip()]


def _build_window(
    sentences: list[str], idx: int, window_size: int
) -> tuple[int, int]:
    """Return the (start, end) slice bounds for the window around ``idx``.

    ``end`` is exclusive. Clamped to [0, len(sentences)].
    """
    start = max(0, idx - window_size)
    end = min(len(sentences), idx + window_size + 1)
    return start, end


def narrative_sentence_window_strategy(
    element: ParsedElement,
) -> list[dict[str, Any]]:
    """Emit one chunk per sentence with ±window-size surrounding context in metadata.

    Each output chunk has:
      - ``text`` — the single sentence (what gets embedded)
      - ``metadata.window_text`` — joined window (what gets shown to the LLM)
      - ``metadata.window_start_idx`` / ``window_end_idx`` — bounds within
        the element (debug + adjacent-chunk merging)
      - ``metadata.sentence_idx`` — this sentence's position
      - All original element metadata preserved

    Short elements (≤1 sentence) fall through to a single chunk where
    ``window_text == text`` so downstream consumers don't need a special case.
    """
    body = element["text"]
    metadata = element.get("metadata", {})
    sentences = split_into_sentences(body)

    if len(sentences) <= 1:
        # Degenerate case — produce one chunk where embedded text and window
        # are identical. Keeps the downstream metadata shape stable.
        text = body.strip()
        if not text:
            return []
        return [
            {
                "text": text,
                "metadata": {
                    "element_type": element["element_type"],
                    "window_text": text,
                    "window_start_idx": 0,
                    "window_end_idx": 1,
                    "sentence_idx": 0,
                    **metadata,
                },
            },
        ]

    window_size = int(getattr(config, "SENTENCE_WINDOW_SIZE", 3))
    chunks: list[dict[str, Any]] = []

    for idx, sentence in enumerate(sentences):
        start, end = _build_window(sentences, idx, window_size)
        window_text = " ".join(sentences[start:end])
        chunks.append(
            {
                "text": sentence,
                "metadata": {
                    "element_type": element["element_type"],
                    "window_text": window_text,
                    "window_start_idx": start,
                    "window_end_idx": end,
                    "sentence_idx": idx,
                    **metadata,
                },
            },
        )

    return chunks


def register_default_strategies() -> None:
    """Register the sentence-window strategy for NarrativeText elements.

    Only called by the chunker registry when ``ENABLE_SENTENCE_WINDOW=true``.
    Idempotent — registering twice on the same element_type emits a warning
    via the registry's existing override-detection.
    """
    # Local import to avoid an import-time cycle through the registry.
    from core.ingest.chunkers import register

    register("NarrativeText", narrative_sentence_window_strategy)
