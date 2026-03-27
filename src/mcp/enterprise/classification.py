# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Classification metadata engine with aggregation-risk detection."""
from __future__ import annotations

import enum
import logging

logger = logging.getLogger("ai-companion.enterprise.classification")


# ---------------------------------------------------------------------------
# Classification levels (ordered low → high)
# ---------------------------------------------------------------------------

class ClassificationLevel(enum.IntEnum):
    """U.S. government-style classification levels, ordered by sensitivity."""

    UNCLASSIFIED = 0
    CUI = 1
    SECRET = 2
    TOP_SECRET = 3
    TS_SCI = 4


# ---------------------------------------------------------------------------
# Chunk classification
# ---------------------------------------------------------------------------

_SOURCE_LEVEL_MAP: dict[str, ClassificationLevel] = {
    "unclassified": ClassificationLevel.UNCLASSIFIED,
    "cui": ClassificationLevel.CUI,
    "secret": ClassificationLevel.SECRET,
    "top_secret": ClassificationLevel.TOP_SECRET,
    "ts_sci": ClassificationLevel.TS_SCI,
}


def classify_chunk(metadata: dict) -> ClassificationLevel:
    """Derive a classification level from source metadata.

    Looks for a ``classification`` key in *metadata*.  Returns
    ``UNCLASSIFIED`` when absent or unrecognized.
    """
    raw = metadata.get("classification", "").lower().strip()
    return _SOURCE_LEVEL_MAP.get(raw, ClassificationLevel.UNCLASSIFIED)


# ---------------------------------------------------------------------------
# Aggregation-risk detection
# ---------------------------------------------------------------------------

def detect_aggregation_risk(chunks: list[dict]) -> list[dict]:
    """Flag when combining chunks from different classification levels
    could imply a higher classification.

    Returns a list of warning dicts with ``chunk_ids``, ``reason``, and
    ``suggested_level``.
    """
    if not chunks:
        return []

    # Classify each chunk and group by level
    levels_seen: dict[ClassificationLevel, list[str]] = {}
    for chunk in chunks:
        level = classify_chunk(chunk.get("metadata", chunk))
        chunk_id = chunk.get("id", chunk.get("chunk_id", "unknown"))
        levels_seen.setdefault(level, []).append(chunk_id)

    # No risk when all chunks are at the same level
    if len(levels_seen) <= 1:
        return []

    warnings: list[dict] = []
    sorted_levels = sorted(levels_seen.keys())
    max_level = sorted_levels[-1]

    # Suggest one level above the highest seen (capped at TS_SCI)
    suggested = min(max_level + 1, ClassificationLevel.TS_SCI)

    # Collect all chunk IDs involved in the cross-level mix
    all_chunk_ids: list[str] = []
    for ids in levels_seen.values():
        all_chunk_ids.extend(ids)

    level_names = [ClassificationLevel(lv).name for lv in sorted_levels]
    warnings.append({
        "chunk_ids": all_chunk_ids,
        "reason": (
            f"Aggregation of chunks across classification levels "
            f"({', '.join(level_names)}) may constitute a higher classification"
        ),
        "suggested_level": ClassificationLevel(suggested).name,
    })

    return warnings
