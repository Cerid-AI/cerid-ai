# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""One filter every retrieval path applies to its results (spec §5, §7).

A result goes when its chunk is forgotten, when the parent chunk it reads from
is forgotten (a parent's passage is what the user saw and chose to forget), or
when its artifact is forgotten. Artifact forgets also archive the node, which
the main query path already honours; this covers the paths that never read the
node, such as the verification retrieval.

``visible`` adds the version rule (spec §7): by default only the current version
of anything with history is returned; with ``as_of`` the version in force then.
"""
from __future__ import annotations

from typing import Any

from core.forget.registry import forgotten_ids
from core.lineage.current import normalize_as_of, valid_at
from core.utils.swallowed import log_swallowed_error


def drop_forgotten(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not results:
        return results
    try:
        chunks = forgotten_ids("chunk")
        artifacts = forgotten_ids("artifact")
    except Exception as exc:  # noqa: BLE001 — an unreadable registry must not take retrieval down
        log_swallowed_error("core.forget.read_filter", exc)
        return results
    if not chunks and not artifacts:
        return results
    return [
        r for r in results
        if r.get("chunk_id") not in chunks
        and (r.get("parent_chunk_id") or None) not in chunks
        and r.get("artifact_id") not in artifacts
    ]


def visible(results: list[dict[str, Any]], as_of: str | None = None) -> list[dict[str, Any]]:
    """What a reader may see: nothing forgotten, and of each lineage only the
    version current now, or in force at ``as_of`` (an ISO date or datetime)."""
    t = normalize_as_of(as_of)
    return [r for r in drop_forgotten(results) if valid_at(r, t)]
