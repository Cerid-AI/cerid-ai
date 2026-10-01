# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A failing lexical arm must not cost a domain its dense hits.

F147: the BM25 and SPLADE searches were gathered with no exception
isolation, so one of them raising reached the whole-domain handler in
``multi_domain_query``, which returned ``[]`` and dropped the vector hits
that had already been fetched. These tests drive the real
``multi_domain_query`` against a fake Chroma client; only the lexical
search functions (the index boundary) are replaced.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import config
from core.agents import query_agent
from core.retrieval import bm25 as bm25_mod
from core.retrieval import sparse_index as sparse_mod

_DOMAIN = "general"
_VECTOR_CHUNK = "art-v1_chunk_0"
_BM25_CHUNK = "art-b1_chunk_0"


class _Collection:
    def count(self) -> int:
        return 2

    def query(self, **_kw: Any) -> dict[str, Any]:
        return {
            "ids": [[_VECTOR_CHUNK]],
            "documents": [["dense hit content"]],
            "distances": [[0.3]],
            "metadatas": [[{"artifact_id": "art-v1", "chunk_index": 0}]],
        }

    def get(self, ids: list[str], **_kw: Any) -> dict[str, Any]:
        return {
            "ids": list(ids),
            "documents": ["keyword hit content" for _ in ids],
            "metadatas": [{"artifact_id": "art-b1", "chunk_index": 0} for _ in ids],
        }


class _Client:
    def list_collections(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=config.collection_name(_DOMAIN))]

    def get_collection(self, name: str) -> _Collection:
        return _Collection()


@pytest.fixture(autouse=True)
def _fresh_count_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(query_agent, "_collection_count_cache", {})


def _raise(*_a: Any, **_kw: Any) -> list[tuple[str, float]]:
    raise RuntimeError("index file is corrupt")


async def _query() -> list[dict[str, Any]]:
    return await query_agent.multi_domain_query(
        query="anything",
        domains=[_DOMAIN],
        top_k=5,
        chroma_client=_Client(),
    )


async def test_bm25_failure_keeps_the_dense_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "HYBRID_FUSION_MODE", "weighted_sum")
    monkeypatch.setattr(bm25_mod, "is_available", lambda: True)
    monkeypatch.setattr(bm25_mod, "search_bm25", _raise)

    results = await _query()

    assert [r["chunk_id"] for r in results] == [_VECTOR_CHUNK]


async def test_sparse_failure_keeps_the_dense_and_bm25_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "HYBRID_FUSION_MODE", "tri_rrf")
    monkeypatch.setattr(bm25_mod, "is_available", lambda: True)
    monkeypatch.setattr(
        bm25_mod, "search_bm25", lambda *_a, **_kw: [(_BM25_CHUNK, 0.9)],
    )
    monkeypatch.setattr(sparse_mod, "is_available", lambda: True)
    monkeypatch.setattr(sparse_mod, "search_sparse", _raise)

    results = await _query()

    assert {r["chunk_id"] for r in results} == {_VECTOR_CHUNK, _BM25_CHUNK}


async def test_lexical_failure_is_counted_as_a_swallowed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[str] = []
    monkeypatch.setattr(
        query_agent, "log_swallowed_error",
        lambda module, _exc, **_kw: recorded.append(module),
    )
    monkeypatch.setattr(config, "HYBRID_FUSION_MODE", "weighted_sum")
    monkeypatch.setattr(bm25_mod, "is_available", lambda: True)
    monkeypatch.setattr(bm25_mod, "search_bm25", _raise)

    await _query()

    assert recorded == ["core.agents.query_agent.lexical_search"]
