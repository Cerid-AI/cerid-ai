# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""E1 post-audit M3 — R16 / CR-001 cache isolation for Memory ON/OFF + C1 scope.

- C2 semantic scope token must separate memory_enabled True vs False.
- C1 exact-match context_hint construction isolates mem/kb/ext/rag and skips
  when metadata_filter / exclude_packs narrow the result (mirrors C2).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from core.retrieval.semantic_cache import _scope_token, cache_lookup, cache_store, set_cache_backend
from utils.query_cache import _cache_key


def test_scope_token_differs_when_memory_off() -> None:
    on = _scope_token(["general"], None, memory_enabled=True)
    off = _scope_token(["general"], None, memory_enabled=False)
    assert on == "general"  # historical token when memory on
    assert off == "general|mem=0"
    assert on != off


def test_scope_token_memory_with_allowed_domains() -> None:
    on = _scope_token(["finance"], ["finance", "general"], memory_enabled=True)
    off = _scope_token(["finance"], ["finance", "general"], memory_enabled=False)
    assert "allow=" in on
    assert off.endswith("|mem=0")
    assert on != off


def test_c2_store_lookup_isolated_by_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Memory-ON store must miss under Memory-OFF lookup (R16)."""
    import threading
    from typing import Any

    class _FakeBackend:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self._ids: list[str] = []
            self._embs: list[np.ndarray] = []
            self._meta: list[dict[str, Any]] = []

        def upsert(self, *, ids, embeddings, metadatas=None) -> None:
            with self._lock:
                metas = metadatas or [{} for _ in ids]
                for eid, emb, m in zip(ids, embeddings, metas):
                    arr = np.asarray(emb, dtype=np.float32)
                    if eid in self._ids:
                        j = self._ids.index(eid)
                        self._embs[j] = arr
                        self._meta[j] = m
                    else:
                        self._ids.append(eid)
                        self._embs.append(arr)
                        self._meta.append(m)

        def query(self, *, query_embeddings, n_results=1, include=None):
            with self._lock:
                if not self._ids:
                    return {"ids": [[]], "distances": [[]]}
                q = np.asarray(query_embeddings[0], dtype=np.float32)
                qn = q / max(np.linalg.norm(q), 1e-12)
                sims = [
                    (eid, float(np.dot(qn, emb / max(np.linalg.norm(emb), 1e-12))))
                    for eid, emb in zip(self._ids, self._embs)
                ]
                sims.sort(key=lambda t: t[1], reverse=True)
                top = sims[:n_results]
                return {
                    "ids": [[t[0] for t in top]],
                    "distances": [[1.0 - t[1] for t in top]],
                }

        def get(self) -> dict:
            return {"ids": list(self._ids)}

        def delete(self, ids=None, where=None) -> None:
            # Was a bare `pass`: it neither rejected an empty `where` (the
            # chromadb 1.x behaviour that hid a production defect) nor actually
            # removed rows, so orphan-eviction assertions passed vacuously.
            # See tests/helpers/fake_chroma.py for the canonical double.
            if where is not None and not ids:
                if not where:
                    raise ValueError(
                        "Expected where to have exactly one operator, got {} in delete"
                    )
                self._ids.clear()
                self._embs.clear()
                return
            for eid in ids or []:
                if eid in self._ids:
                    j = self._ids.index(eid)
                    del self._ids[j]
                    del self._embs[j]

        def count(self) -> int:
            return len(self._ids)

    backend = _FakeBackend()
    set_cache_backend(backend)
    redis = MagicMock()
    store: dict[str, str] = {}

    def _setex(key, ttl, val):  # noqa: ARG001
        store[key] = val

    def _get(key):
        return store.get(key)

    redis.setex.side_effect = _setex
    redis.get.side_effect = _get

    emb = np.ones(8, dtype=np.float32)
    emb = emb / np.linalg.norm(emb)
    payload = {"sources": [{"id": 1}], "results": [{"relevance": 0.9}], "context": "mem"}

    cache_store(
        "what did I say?", emb, payload, redis,
        domains=["general"], memory_enabled=True,
    )
    # Same embedding, memory off → miss
    hit_off = cache_lookup(
        emb, redis, domains=["general"], memory_enabled=False, threshold=0.5,
    )
    assert hit_off is None

    hit_on = cache_lookup(
        emb, redis, domains=["general"], memory_enabled=True, threshold=0.5,
    )
    assert hit_on is not None
    assert hit_on.get("context") == "mem"

    set_cache_backend(None)


def test_c1_key_differs_by_memory_and_rag() -> None:
    base = "all|rerank=True"
    k_mem = _cache_key("q", base, 10, "gui|mem=1|kb=1|ext=1|rag=manual|mf=")
    k_nomem = _cache_key("q", base, 10, "gui|mem=0|kb=1|ext=1|rag=manual|mf=")
    k_smart = _cache_key("q", base, 10, "gui|mem=1|kb=1|ext=1|rag=smart|mf=")
    assert k_mem != k_nomem
    assert k_mem != k_smart
