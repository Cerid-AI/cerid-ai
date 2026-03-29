# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Content-level junk detection — filters low-value text before chunking.

Called during folder scan to avoid wasting embedding compute and storage
on gibberish, binary content, or auto-generated boilerplate.
"""
from __future__ import annotations


def is_junk_content(text: str, *, min_length: int = 50) -> tuple[bool, str]:
    """Check if text content is junk and should be skipped.

    Returns (is_junk, reason) where reason explains why content was rejected.
    """
    if not text or not text.strip():
        return True, "empty content"

    stripped = text.strip()

    # Too short to be useful
    if len(stripped) < min_length:
        return True, f"too short ({len(stripped)} chars)"

    # Binary content (null bytes)
    sample = text[:512]
    if "\x00" in sample:
        return True, "binary content detected"

    # Encoding garbage (high ratio of replacement chars)
    replacement_count = text.count("\ufffd")
    if len(text) > 100 and replacement_count / len(text) > 0.1:
        return True, f"encoding errors ({replacement_count} replacement chars)"

    # Extremely repetitive (single word/char repeated)
    words = stripped.split()
    if len(words) > 10:
        unique_ratio = len(set(w.lower() for w in words)) / len(words)
        if unique_ratio < 0.05:
            return True, f"repetitive content ({unique_ratio:.0%} unique words)"

    return False, ""
