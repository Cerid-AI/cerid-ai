# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Recall returns the current version of each memory, or the one in force at
``as_of`` (forget phase 5, spec §7).

The lineage writer stamps ``valid_to`` and ``superseded_by`` on every Chroma row
of a version it closes, so recall reads them from the rows it already has and
the shared read filter decides; there is no graph round-trip and no flag.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.agents.memory import recall_memories

_HIGH_ENTAILMENT = {"entailment": 0.9, "neutral": 0.05, "contradiction": 0.05}


def _chroma(rows: dict[str, dict[str, str]]) -> MagicMock:
    ids = list(rows)
    metas = [
        {"artifact_id": aid, "memory_type": "empirical", "valid_from": "2025-01-01T00:00:00Z",
         "access_count": "0", "summary": f"summary-{aid}", **extra}
        for aid, extra in rows.items()
    ]
    collection = MagicMock()
    collection.query.return_value = {
        "ids": [ids], "documents": [[f"doc {aid}" for aid in ids]],
        "distances": [[0.1] * len(ids)], "metadatas": [metas],
    }
    client = MagicMock()
    client.get_or_create_collection.return_value = collection
    return client


async def _recall(rows: dict[str, dict[str, str]], **kw) -> set[str]:
    with patch("core.utils.nli.nli_score", return_value=_HIGH_ENTAILMENT):
        results = await recall_memories("q", _chroma(rows), None, top_k=10, **kw)
    return {m["memory_id"] for m in results}


@pytest.mark.asyncio
async def test_recall_returns_only_the_current_version() -> None:
    got = await _recall({
        "old": {"valid_to": "2026-05-01", "superseded_by": "new", "lineage_id": "old", "version": "1"},
        "new": {"valid_from": "2026-05-01", "valid_to": "", "lineage_id": "old", "version": "2"},
        "unversioned": {},  # written before lineages: current
    })
    assert got == {"new", "unversioned"}


@pytest.mark.asyncio
async def test_a_row_marked_only_superseded_is_history_too() -> None:
    assert await _recall({"a": {"superseded_by": "b"}, "b": {}}) == {"b"}


@pytest.mark.asyncio
async def test_as_of_returns_the_version_in_force_then() -> None:
    rows = {
        "old": {"valid_from": "2025-01-01", "valid_to": "2026-05-01", "superseded_by": "new"},
        "new": {"valid_from": "2026-05-01", "valid_to": ""},
    }
    assert await _recall(rows, as_of="2026-03-15") == {"old"}
    assert await _recall(rows, as_of="2026-06-01") == {"new"}
    assert await _recall(rows, as_of="2026-05-01") == {"new"}  # a date covers the whole day


@pytest.mark.asyncio
async def test_a_forgotten_memory_is_never_recalled() -> None:
    with patch("core.forget.read_filter.forgotten_ids",
               side_effect=lambda kind: frozenset({"gone"}) if kind == "artifact" else frozenset()):
        assert await _recall({"gone": {}, "kept": {}}) == {"kept"}


@pytest.mark.asyncio
async def test_recall_disabled_by_toggle(monkeypatch) -> None:
    monkeypatch.setattr("config.ENABLE_MEMORY_RECALL", False)
    assert await _recall({"a": {}}) == set()
