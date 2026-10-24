# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Counts for local model calls. Not a second router.

The ladder is the stage profiles that already exist. This module only
remembers how many local attempts ran, how many tokens the server reported,
and how many came back truncated (``done_reason`` ``length``) or failed.
A truncated reply is still returned to the caller. The count is not a price.
"""

from __future__ import annotations

from threading import Lock
from typing import Any

_lock = Lock()
_stats = {
    "attempts": 0,
    "tokens": 0,
    "truncated": 0,
    "failed": 0,
}


def token_count(payload: dict[str, Any]) -> int:
    """Tokens the local server reported. Missing or junk fields count as zero."""
    total = 0
    for key in ("eval_count", "prompt_eval_count"):
        raw = payload.get(key)
        if raw is None:
            continue
        try:
            total += int(raw)
        except (TypeError, ValueError):
            continue
    return max(0, total)


def record_local_attempt(
    *,
    tokens: int = 0,
    truncated: bool = False,
    failed: bool = False,
) -> None:
    with _lock:
        _stats["attempts"] += 1
        _stats["tokens"] += max(0, int(tokens))
        if truncated:
            _stats["truncated"] += 1
        if failed:
            _stats["failed"] += 1


def note_local_completion(payload: dict[str, Any]) -> None:
    """Record one completed local response, including a length truncation.

    The caller still returns the text. Truncation is a failed attempt in
    the count and a delivered attempt in the product.
    """
    truncated = str(payload.get("done_reason") or "") == "length"
    record_local_attempt(
        tokens=token_count(payload),
        truncated=truncated,
        failed=truncated,
    )


def snapshot() -> dict[str, int]:
    with _lock:
        return dict(_stats)


def reset() -> None:
    """Test helper. Production never clears the count mid-process."""
    with _lock:
        for key in _stats:
            _stats[key] = 0
