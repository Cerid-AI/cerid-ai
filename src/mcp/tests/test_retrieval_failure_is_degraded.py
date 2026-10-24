# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""A domain search that fails is a degraded retrieval, not an empty knowledge
base. Measured live (audit 2026-10-10, F10): the local embedder stopped
answering, every domain returned [] and the response said nothing — the user
saw "nothing in your knowledge base" and CRAG answered from the web."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import core.agents.query_agent as qa


class _Refusing:
    name = "domain_coding"

    def count(self):
        return 10

    def query(self, **kwargs):
        raise RuntimeError("Quenchforge embed failed (ConnectTimeout) and the fallback leg runs another space")


class _Client:
    def list_collections(self):
        return [SimpleNamespace(name="domain_coding"), SimpleNamespace(name="domain_general")]

    def get_collection(self, name):
        return _Refusing()


def _search():
    qa._collection_count_cache.clear()
    with patch("core.retrieval.bm25.is_available", return_value=False):
        return asyncio.run(_with_failures(qa.multi_domain_query("q", domains=["coding", "general"], chroma_client=_Client())))


async def _with_failures(coro):
    qa._domain_failures.set([])
    results = await coro
    return results, qa.domain_failure_reason(2)


def test_a_failed_domain_search_is_reported_not_silent():
    results, reason = _search()
    assert results == []
    assert reason.startswith("Searching 2 of 2 knowledge-base domains failed (the embedding service did not answer)")


def test_no_failure_no_reason():
    async def run():
        qa._domain_failures.set([])
        return qa.domain_failure_reason(3)

    assert asyncio.run(run()) == ""


def test_a_degraded_result_is_never_cached(monkeypatch):
    from core.retrieval import semantic_cache

    stored = []

    class _Backend:
        def __getattr__(self, name):
            return lambda *a, **k: stored.append(name)

    monkeypatch.setattr(semantic_cache, "_get_backend", lambda: _Backend())
    semantic_cache.cache_store("q", [0.1, 0.2], {"sources": [{"artifact_id": "a"}], "retrieval_degraded": True}, object())
    assert stored == []
