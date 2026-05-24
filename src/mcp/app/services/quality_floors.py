# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-source quality floors.

Each (:Source) node carries a ``quality_floor`` float in [0.0, 1.0].
Artifacts whose computed weighted quality score falls below their
source's floor are dropped before chunking + embedding. This is the
operator's lever for noise control on noisy sources (RSS feeds, chat
captures with low-signal-to-noise).

The legacy `SCAN_MIN_QUALITY` global stays in force as a *floor of
floors* — the more restrictive of the two wins.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("ai-companion.services.quality_floors")

_CACHE: dict[str, float] = {}


def get_source_quality_floor(source_id: str | None) -> float:
    """Return the source's quality_floor, or 0.0 when unset / missing.

    Result is memoized per source for the process lifetime — sources
    rarely change their floor and the lookup is on the hot path.
    Call :func:`invalidate_cache` after editing a source.
    """
    if not source_id:
        return 0.0
    if source_id in _CACHE:
        return _CACHE[source_id]

    try:
        from app.db.neo4j import sources as srcdb
        from app.deps import get_neo4j

        src = srcdb.get_source(get_neo4j(), source_id)
        if src is None:
            return 0.0
        floor = float(src.get("quality_floor", 0.0) or 0.0)
        _CACHE[source_id] = floor
        return floor
    except Exception as exc:  # noqa: BLE001 — defensive cache miss
        logger.debug("get_source_quality_floor lookup failed: %s", exc)
        return 0.0


def should_drop(source_id: str | None, quality_score: float) -> bool:
    """Return True iff the artifact's quality_score falls below the
    source's configured floor. Sources without a configured floor
    always return False.
    """
    floor = get_source_quality_floor(source_id)
    if floor <= 0.0:
        return False
    return quality_score < floor


def invalidate_cache(source_id: str | None = None) -> None:
    """Clear the floor cache. Call after a source's quality_floor edit.

    When ``source_id`` is None, clears the whole cache (used by tests).
    """
    if source_id is None:
        _CACHE.clear()
        return
    _CACHE.pop(source_id, None)


def set_source_quality_floor(source_id: str, floor: float) -> dict[str, Any]:
    """Persist a new quality_floor for a source and invalidate the
    cache so the next ingestion call picks up the new value.

    Returns the updated source record.
    """
    if not 0.0 <= floor <= 1.0:
        raise ValueError(f"quality_floor must be in [0.0, 1.0], got {floor}")

    from app.deps import get_neo4j

    driver = get_neo4j()
    with driver.session() as session:
        record = session.run(
            """
            MATCH (s:Source {id: $id})
            SET s.quality_floor = $floor
            RETURN s
            """,
            id=source_id,
            floor=floor,
        ).single()

    invalidate_cache(source_id)
    if record is None:
        raise ValueError(f"Source not found: {source_id}")

    from app.db.neo4j.sources import _node_to_dict

    return _node_to_dict(record["s"])
