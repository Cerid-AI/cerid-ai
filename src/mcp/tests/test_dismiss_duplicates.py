# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""AF-028 — dismissed duplicate groups persist and are filtered from list_duplicates.

Previously dismiss_duplicates returned ok but persisted nothing, so a dismissed
group reappeared on the next fetch. Now the group's shared content_hash is
stored in a Redis set that list_duplicates filters against.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.routers.kb_admin import (
    DismissDuplicatesRequest,
    dismiss_duplicates,
    list_duplicates,
)


class _FakeRedis:
    def __init__(self) -> None:
        self._sets: dict[str, set] = {}

    def sadd(self, key: str, *vals: str) -> int:
        self._sets.setdefault(key, set()).update(vals)
        return len(vals)

    def smembers(self, key: str) -> set:
        return set(self._sets.get(key, set()))


def _row(aid: str, ch: str) -> dict:
    return {
        "id": aid, "content_hash": ch, "filename": f"{aid}.md",
        "domain": "general", "summary": "", "quality_score": None,
        "ingested_at": "", "chunk_count": 1,
    }


# Two groups: A (a1,a2 share hashA), B (b1,b2 share hashB).
_ROWS = [_row("a1", "hashA"), _row("a2", "hashA"),
         _row("b1", "hashB"), _row("b2", "hashB")]


def _hashes(resp) -> set[str]:
    # The response exposes only a 12-char prefix; both test hashes are short.
    return {g.content_hash_prefix for g in resp.groups}


@pytest.mark.asyncio
async def test_list_returns_all_groups_when_none_dismissed():
    redis = _FakeRedis()
    with (
        patch("app.routers.kb_admin.get_neo4j", return_value=MagicMock()),
        patch("app.routers.kb_admin.get_redis", return_value=redis),
        patch("app.routers.kb_admin.list_duplicate_artifacts", return_value=_ROWS),
    ):
        resp = await list_duplicates()
    assert resp.total_groups == 2
    assert _hashes(resp) == {"hashA", "hashB"}


@pytest.mark.asyncio
async def test_dismiss_persists_group_content_hash():
    redis = _FakeRedis()
    with (
        patch("app.routers.kb_admin.get_neo4j", return_value=MagicMock()),
        patch("app.routers.kb_admin.get_redis", return_value=redis),
        patch("app.routers.kb_admin.list_duplicate_artifacts", return_value=_ROWS),
    ):
        resp = await dismiss_duplicates(DismissDuplicatesRequest(artifact_ids=["a1", "a2"]))
    assert resp["dismissed"] == 2
    # The group's shared hash is persisted (not the individual artifact ids).
    assert redis.smembers("cerid:kb:dismissed_duplicate_hashes") == {"hashA"}


@pytest.mark.asyncio
async def test_dismiss_then_list_omits_the_group():
    redis = _FakeRedis()
    with (
        patch("app.routers.kb_admin.get_neo4j", return_value=MagicMock()),
        patch("app.routers.kb_admin.get_redis", return_value=redis),
        patch("app.routers.kb_admin.list_duplicate_artifacts", return_value=_ROWS),
    ):
        await dismiss_duplicates(DismissDuplicatesRequest(artifact_ids=["a1", "a2"]))
        resp = await list_duplicates()
    # Group A is dismissed → only B remains (the reappear bug is fixed).
    assert resp.total_groups == 1
    assert _hashes(resp) == {"hashB"}


@pytest.mark.asyncio
async def test_list_no_redis_returns_all_groups():
    """Graceful degradation: without Redis, nothing is filtered."""
    with (
        patch("app.routers.kb_admin.get_neo4j", return_value=MagicMock()),
        patch("app.routers.kb_admin.get_redis", return_value=None),
        patch("app.routers.kb_admin.list_duplicate_artifacts", return_value=_ROWS),
    ):
        resp = await list_duplicates()
    assert resp.total_groups == 2
