# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Which rows are current: one rule for every kind (spec §7).

A version is current while its ``valid_to`` is empty. Chroma stores an open
interval as ``""`` and Neo4j as null; ``superseded_by`` is checked too so a row
stamped by an older writer that set only that field still counts as closed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

#: Chroma metadata flag on every row of a closed version (1) or a current one
#: (0). A row written before lineages has no flag and counts as current.
VERSION_CLOSED = "version_closed"
CURRENT_ONLY_WHERE: dict[str, Any] = {VERSION_CLOSED: {"$ne": 1}}

# Searches for supersession candidates fetch this many times what they keep,
# so closed versions and transcript rows filtered out afterwards do not leave
# the candidate list short.
CANDIDATE_OVERFETCH = 4


def is_current(meta: dict[str, Any] | None) -> bool:
    m = meta or {}
    return not (m.get("valid_to") or m.get("superseded_by"))


def is_current_memory(meta: dict[str, Any] | None) -> bool:
    """A current memory row: transcripts in the same collection carry no
    ``memory_type`` and are never supersession candidates."""
    return bool((meta or {}).get("memory_type")) and is_current(meta)


_DATE_ONLY_LEN = len("YYYY-MM-DD")


def parse_time(value: Any) -> datetime | None:
    """A stored or requested time as an aware UTC datetime. A bare date is the
    start of that day; a time without an offset is UTC; anything unreadable is
    None."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def version_fields(meta: dict[str, Any] | None) -> dict[str, Any]:
    """The lineage fields a result row carries, from its Chroma metadata."""
    m = meta or {}
    return {
        "valid_from": str(m.get("valid_from") or ""),
        "valid_to": str(m.get("valid_to") or ""),
        "superseded_by": str(m.get("superseded_by") or ""),
        "lineage_id": str(m.get("lineage_id") or ""),
        "version": int(m.get("version") or 1),
    }


def normalize_as_of(as_of: str | None) -> datetime | None:
    """An ``as_of`` date covers that whole day; a datetime is used as given."""
    if not as_of:
        return None
    t = as_of.strip()
    if len(t) == _DATE_ONLY_LEN:
        day = parse_time(t)
        return day + timedelta(days=1) - timedelta(microseconds=1) if day else None
    return parse_time(t)


def valid_at(row: dict[str, Any], as_of: datetime | None) -> bool:
    """Whether a row was the version in force at ``as_of``, or is current when
    ``as_of`` is None. Only a stated ``valid_from`` bounds a row from below:
    when a document was ingested says nothing about when it was true, so a row
    without one counts as in force from the start. A ``valid_to`` that cannot be
    read counts as closed."""
    if as_of is None:
        return is_current(row)
    start = parse_time(row.get("valid_from"))
    if start is not None and start > as_of:
        return False
    end_text = str(row.get("valid_to") or "")
    if not end_text:
        return True
    end = parse_time(end_text)
    return end is not None and end > as_of


#: What an ``as_of`` field accepts: an ISO date, or an ISO datetime.
AS_OF_PATTERN = r"^\d{4}-\d{2}-\d{2}([T ][0-9:.]+(Z|[+-]\d{2}:?\d{2})?)?$"
AS_OF_DESCRIPTION = (
    "Answer as of this date (ISO date or datetime): return the version of each memory or document "
    "that was in force then instead of the current one. Omit for current knowledge."
)
