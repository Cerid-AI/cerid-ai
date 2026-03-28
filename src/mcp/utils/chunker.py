# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Text chunking with token-based and semantic modes.

Semantic mode splits on paragraph boundaries and preserves sentence integrity,
producing higher-quality chunks for RAG retrieval. Token mode is the original
fixed-window approach with overlap.

Contextual headers can be prepended to each chunk to improve retrieval quality
by providing the embedding model with source context.
"""

from __future__ import annotations

import os
import re

import tiktoken

from config.constants import CHUNK_OVERLAP_RATIO

_ENCODING = tiktoken.get_encoding("cl100k_base")

# Sentence boundary regex — handles common abbreviations to avoid false splits
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def count_tokens(text: str) -> int:
    """Count tokens using cl100k_base (GPT-4 / embedding models)."""
    return len(_ENCODING.encode(text))


# ---------------------------------------------------------------------------
# Contextual header
# ---------------------------------------------------------------------------

def make_context_header(
    *,
    filename: str = "",
    domain: str = "",
    sub_category: str = "",
) -> str:
    """Build a contextual header string for prepending to chunks.

    Example output:
        "Source: report.pdf | Domain: finance | Category: tax-returns"
    """
    parts: list[str] = []
    if filename:
        parts.append(f"Source: {filename}")
    if domain:
        parts.append(f"Domain: {domain}")
    if sub_category:
        parts.append(f"Category: {sub_category}")
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Token-based chunking (original)
# ---------------------------------------------------------------------------

def chunk_text_token(
    text: str,
    max_tokens: int = 512,
    overlap: float = CHUNK_OVERLAP_RATIO,
) -> list[str]:
    """Split text into token-limited chunks with overlap."""
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


# ---------------------------------------------------------------------------
# Semantic chunking
# ---------------------------------------------------------------------------

def _split_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs (double-newline separated).

    Preserves table blocks (lines starting with |) as single paragraphs.
    """
    raw = re.split(r"\n{2,}", text.strip())
    paragraphs: list[str] = []
    table_buffer: list[str] = []

    for block in raw:
        block = block.strip()
        if not block:
            continue
        # Detect Markdown table blocks
        lines = block.split("\n")
        is_table = all(line.strip().startswith("|") for line in lines if line.strip())
        if is_table:
            table_buffer.append(block)
        else:
            if table_buffer:
                # Flush table with surrounding context
                paragraphs.append("\n\n".join(table_buffer))
                table_buffer = []
            paragraphs.append(block)

    if table_buffer:
        paragraphs.append("\n\n".join(table_buffer))

    return paragraphs


def chunk_text_semantic(
    text: str,
    max_tokens: int = 512,
    overlap: float = CHUNK_OVERLAP_RATIO,
) -> list[str]:
    """Split text into chunks on paragraph boundaries, respecting token limits.

    Strategy:
    1. Split into paragraphs (double-newline delimited).
    2. Greedily accumulate paragraphs into chunks up to max_tokens.
    3. If a single paragraph exceeds max_tokens, fall back to sentence
       splitting, then to token-based splitting as last resort.
    4. Overlap is applied by repeating the last paragraph of the previous chunk.
    """
    total_tokens = count_tokens(text)
    if total_tokens <= max_tokens:
        return [text]

    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return [text] if text.strip() else []

    chunks: list[str] = []
    current_parts: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = count_tokens(para)

        # If this paragraph alone exceeds max_tokens, split it further
        if para_tokens > max_tokens:
            # Flush current accumulator first
            if current_parts:
                chunks.append("\n\n".join(current_parts))
                current_parts = []
                current_tokens = 0

            # Try sentence splitting
            sentences = _SENTENCE_RE.split(para)
            if len(sentences) > 1:
                sent_parts: list[str] = []
                sent_tokens = 0
                for sent in sentences:
                    st = count_tokens(sent)
                    if sent_tokens + st > max_tokens and sent_parts:
                        chunks.append(" ".join(sent_parts))
                        sent_parts = []
                        sent_tokens = 0
                    sent_parts.append(sent)
                    sent_tokens += st
                if sent_parts:
                    chunks.append(" ".join(sent_parts))
            else:
                # Last resort: token-based split for very long paragraphs
                chunks.extend(chunk_text_token(para, max_tokens, overlap))
            continue

        # Would adding this paragraph exceed the limit?
        if current_tokens + para_tokens > max_tokens and current_parts:
            chunks.append("\n\n".join(current_parts))
            # Overlap: keep the last paragraph as context for the next chunk
            last_part = current_parts[-1]
            last_tokens = count_tokens(last_part)
            if last_tokens <= int(max_tokens * overlap):
                current_parts = [last_part]
                current_tokens = last_tokens
            else:
                current_parts = []
                current_tokens = 0

        current_parts.append(para)
        current_tokens += para_tokens

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks


# ---------------------------------------------------------------------------
# Parent-child chunking
# ---------------------------------------------------------------------------

PARENT_CHILD_ENABLED = os.getenv(
    "ENABLE_PARENT_CHILD_RETRIEVAL", "false"
).lower() in ("true", "1")

# Child chunk size: parent max // 4 (~128 tokens for 512-token parents)
_CHILD_CHUNK_TOKENS = 128
_CHILD_OVERLAP_RATIO = 0.1  # 10% overlap for small child chunks


def chunk_with_parents(
    text: str,
    artifact_id: str,
    max_tokens: int = 512,
    overlap: float = CHUNK_OVERLAP_RATIO,
    mode: str | None = None,
    context_header: str = "",
) -> list[dict]:
    """Create a two-tier chunking hierarchy (parent + child chunks).

    Parent chunks are created at *max_tokens* granularity using the standard
    chunking pipeline.  Each parent is then split into smaller child chunks
    (~128 tokens) that are used for search precision while the parent provides
    generation context.

    Returns a flat list of dicts, each with keys:
        chunk_id, text, chunk_level ("parent" | "child"),
        parent_chunk_id, child_index, parent_token_count
    """
    if not PARENT_CHILD_ENABLED:
        # Feature flag off — fall back to standard flat chunking.
        raw = chunk_text(
            text,
            max_tokens=max_tokens,
            overlap=overlap,
            mode=mode,
            context_header=context_header,
        )
        return [
            {
                "chunk_id": f"{artifact_id}_chunk_{i}",
                "text": c,
                "chunk_level": "flat",
                "parent_chunk_id": None,
                "child_index": None,
                "parent_token_count": None,
            }
            for i, c in enumerate(raw)
        ]

    # --- Step 1: create parent chunks (standard pipeline) ----------------
    parent_texts = chunk_text(
        text,
        max_tokens=max_tokens,
        overlap=overlap,
        mode=mode,
        context_header=context_header,
    )

    results: list[dict] = []

    for parent_idx, parent_text in enumerate(parent_texts):
        parent_id = f"{artifact_id}_parent_{parent_idx}"
        parent_token_count = count_tokens(parent_text)

        # Emit the parent chunk
        results.append(
            {
                "chunk_id": parent_id,
                "text": parent_text,
                "chunk_level": "parent",
                "parent_chunk_id": parent_id,
                "child_index": None,
                "parent_token_count": parent_token_count,
            }
        )

        # --- Step 2: split parent into child chunks ----------------------
        child_texts = chunk_text_token(
            parent_text,
            max_tokens=_CHILD_CHUNK_TOKENS,
            overlap=_CHILD_OVERLAP_RATIO,
        )

        for child_idx, child_text in enumerate(child_texts):
            child_id = f"{artifact_id}_child_{parent_idx}_{child_idx}"
            results.append(
                {
                    "chunk_id": child_id,
                    "text": child_text,
                    "chunk_level": "child",
                    "parent_chunk_id": parent_id,
                    "child_index": child_idx,
                    "parent_token_count": parent_token_count,
                }
            )

    return results


def get_parent_chunks(
    child_chunk_ids: list[str],
    all_chunks: list[dict],
) -> list[dict]:
    """Given child chunk IDs, return the corresponding unique parent chunks."""
    # Build a lookup: parent_chunk_id → parent dict
    parent_map: dict[str, dict] = {}
    for chunk in all_chunks:
        if chunk["chunk_level"] == "parent":
            parent_map[chunk["chunk_id"]] = chunk

    # Collect parent IDs referenced by the requested children
    child_lookup = {c["chunk_id"]: c for c in all_chunks}
    seen: set[str] = set()
    parents: list[dict] = []
    for cid in child_chunk_ids:
        child = child_lookup.get(cid)
        if child is None:
            continue
        pid = child.get("parent_chunk_id")
        if pid and pid not in seen:
            seen.add(pid)
            parent = parent_map.get(pid)
            if parent:
                parents.append(parent)
    return parents


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def chunk_text(
    text: str,
    max_tokens: int = 512,
    overlap: float = CHUNK_OVERLAP_RATIO,
    mode: str | None = None,
    context_header: str = "",
) -> list[str]:
    """Split text into chunks using the configured chunking mode.

    Args:
        text: The full text to chunk.
        max_tokens: Maximum tokens per chunk.
        overlap: Fraction of max_tokens to overlap between consecutive chunks.
        mode: "token" or "semantic". Defaults to config.CHUNKING_MODE.
        context_header: Optional contextual header to prepend to each chunk.
    """
    if mode is None:
        import config
        mode = getattr(config, "CHUNKING_MODE", "semantic")

    # Reserve tokens for the context header
    header_tokens = count_tokens(context_header) if context_header else 0
    effective_max = max_tokens - header_tokens
    if effective_max < 50:
        effective_max = 50  # safety floor

    if mode == "semantic":
        raw_chunks = chunk_text_semantic(text, effective_max, overlap)
    else:
        raw_chunks = chunk_text_token(text, effective_max, overlap)

    if context_header:
        return [f"{context_header}\n\n{chunk}" for chunk in raw_chunks]
    return raw_chunks
