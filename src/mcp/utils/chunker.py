# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Token-based text chunking with configurable overlap.
"""

from __future__ import annotations

from typing import List

import tiktoken

_ENCODING = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Count tokens using cl100k_base (GPT-4 / embedding models)."""
    return len(_ENCODING.encode(text))


def chunk_text(
    text: str,
    max_tokens: int = 512,
    overlap: float = 0.2,
) -> List[str]:
    """
    Split text into token-limited chunks with overlap.

    Args:
        text: The full text to chunk.
        max_tokens: Maximum tokens per chunk.
        overlap: Fraction of max_tokens to overlap between consecutive chunks.

    Returns:
        List of text chunks. Single-element list if text fits in one chunk.
    """
    tokens = _ENCODING.encode(text)
    total = len(tokens)

    if total <= max_tokens:
        return [text]

    overlap_tokens = int(max_tokens * overlap)
    step = max_tokens - overlap_tokens
    if step < 1:
        step = 1

    chunks = []
    start = 0
    while start < total:
        end = min(start + max_tokens, total)
        chunk_tokens = tokens[start:end]
        chunks.append(_ENCODING.decode(chunk_tokens))
        if end >= total:
            break
        start += step

    return chunks
