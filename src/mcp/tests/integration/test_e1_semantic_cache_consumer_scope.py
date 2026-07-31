# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-1e verifiability harness — C2 SEMANTIC-CACHE CONSUMER-SCOPE probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 1.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-001, the C2 half).

CR-001 (critical) has two cache layers. Phase 1c closed the C1 exact-match cache
(``utils/query_cache``) by folding the consumer's effective domain scope into the
key. This probe covers the C2 SEMANTIC cache
(``core/retrieval/semantic_cache``): its scope token was keyed on the raw
incoming ``domains`` only and omitted consumer identity, so a strict consumer
(``cerid-finance``, allowed_domains=[finance,general]) issuing the same — or a
paraphrase at cosine ≥ 0.92 — query as the unrestricted ``gui`` consumer received
gui's cached answer verbatim, the allowed_domains filter never running because
the cache short-circuits before it.

These are **synthetic** unit-level probes — no live stack. They drive the REAL
``cache_store`` / ``cache_lookup`` against a brute-force-cosine fake backend and a
fake redis, storing under one consumer's scope and looking up under another's.

RED-then-GREEN: written against today's (pre-fix) code where ``_scope_token``
ignores ``allowed_domains`` — the cross-consumer assertion is RED. The Phase-1e
fix threads ``allowed_domains`` into the scope token (unchanged when None, so
unrestricted callers and legacy entries keep matching — no cache flush). Once
GREEN these are live ``@pytest.mark.preservation`` gates: a regression that drops
consumer scoping from the C2 key fails the merge. The green anchors guard against
OVER-isolation (same-consumer and unrestricted-consumer sharing must survive).
"""
from __future__ import annotations

import threading
from typing import Any

import fakeredis
import numpy as np
import pytest

from core.retrieval import semantic_cache
from core.retrieval.semantic_cache import cache_lookup, cache_store, set_cache_backend

_FINANCE = ["finance", "general"]


def _embedding(dim: int = 768, seed: int = 7) -> np.ndarray:
    rng = np.random.RandomState(seed)
    emb = rng.randn(dim).astype(np.float32)
    return emb / np.linalg.norm(emb)


class _FakeBackend:
    """Duck-typed _CacheBackend: brute-force cosine, distance-ordered results."""

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
            return {"ids": [[t[0] for t in top]], "distances": [[1.0 - t[1] for t in top]]}

    def get(self) -> dict:
        with self._lock:
            return {"ids": list(self._ids)}

    def delete(self, ids=None, where=None) -> None:
        with self._lock:
            if where is not None and not ids:
                # chromadb 1.x REJECTS an empty `where` instead of treating it
                # as clear-all. Mirroring the 0.5 semantics here let the
                # production clear-all path throw on every mutation while this
                # (preservation-marked) gate stayed green.
                if not where:
                    raise ValueError(
                        "Expected where to have exactly one operator, got {} in delete"
                    )
                self._ids.clear()
                self._embs.clear()
                self._meta.clear()
                return
            for eid in ids or []:
                if eid in self._ids:
                    j = self._ids.index(eid)
                    del self._ids[j]
                    del self._embs[j]
                    del self._meta[j]

    def count(self) -> int:
        with self._lock:
            return len(self._ids)


@pytest.fixture
def backend():
    be = _FakeBackend()
    set_cache_backend(be)
    yield be
    set_cache_backend(None)


def _store_result(text: str):
    return {"context": text, "sources": [{"filename": "kb.md"}], "results": [{"id": "a1"}]}


# ---------------------------------------------------------------------------
# The leak: a strict consumer must NOT receive an unrestricted consumer's entry.
# ---------------------------------------------------------------------------

@pytest.mark.preservation
def test_c2_semantic_cache_does_not_leak_across_consumers(backend):
    """gui (allowed_domains=None) caches an all-domains answer; cerid-finance
    (strict, allowed_domains=[finance,general]) issuing the SAME embedding under
    the SAME domain filter must NOT receive it — the C2 scope must carry the
    consumer's effective domain wall (CR-001)."""
    redis = fakeredis.FakeRedis(decode_responses=True)
    emb = _embedding()

    # gui stores an unrestricted answer (allowed_domains=None).
    cache_store(
        "who owns the trading book?", emb, _store_result("gui all-domain answer"),
        redis, ttl=300, domains=None, allowed_domains=None,
    )

    # finance issues the same query embedding under the same requested domains,
    # but is contractually walled to [finance, general].
    hit = cache_lookup(emb, redis, threshold=0.9, domains=None, allowed_domains=_FINANCE)
    assert hit is None, (
        "strict consumer cerid-finance received gui's unrestricted cached answer "
        "via the C2 semantic cache — consumer domain isolation bypassed (CR-001)"
    )


@pytest.mark.preservation
def test_c2_reverse_direction_no_leak(backend):
    """The mirror: an unrestricted consumer must not receive a strict consumer's
    scoped entry either."""
    redis = fakeredis.FakeRedis(decode_responses=True)
    emb = _embedding(seed=11)

    cache_store(
        "finance q", emb, _store_result("finance-scoped answer"),
        redis, ttl=300, domains=None, allowed_domains=_FINANCE,
    )
    hit = cache_lookup(emb, redis, threshold=0.9, domains=None, allowed_domains=None)
    assert hit is None, (
        "gui received cerid-finance's scoped cached answer via the C2 semantic "
        "cache (CR-001)"
    )


# ---------------------------------------------------------------------------
# Green anchors — the fix must NOT over-isolate.
# ---------------------------------------------------------------------------

@pytest.mark.preservation
def test_c2_same_strict_consumer_still_hits(backend):
    """A strict consumer issuing an identical query must still get its own warm
    hit — keying by consumer scope must not defeat caching for that consumer."""
    redis = fakeredis.FakeRedis(decode_responses=True)
    emb = _embedding(seed=5)

    cache_store(
        "finance q", emb, _store_result("finance answer"),
        redis, ttl=300, domains=["finance"], allowed_domains=_FINANCE,
    )
    hit = cache_lookup(emb, redis, threshold=0.9, domains=["finance"], allowed_domains=_FINANCE)
    assert hit is not None and hit["context"] == "finance answer", (
        "same strict consumer did not get a warm C2 hit — over-isolated"
    )


@pytest.mark.preservation
def test_c2_unrestricted_consumers_share(backend):
    """Two unrestricted callers (allowed_domains=None — gui, a2a-agent, _default)
    have the identical retrieval wall, so they must still share the entry.
    Also proves the None case is byte-identical to the historical domain-only
    token (no cache flush on deploy, legacy entries keep matching)."""
    redis = fakeredis.FakeRedis(decode_responses=True)
    emb = _embedding(seed=9)

    cache_store(
        "shared q", emb, _store_result("shared answer"),
        redis, ttl=300, domains=["notes"], allowed_domains=None,
    )
    hit = cache_lookup(emb, redis, threshold=0.9, domains=["notes"], allowed_domains=None)
    assert hit is not None and hit["context"] == "shared answer", (
        "two unrestricted callers failed to share a C2 entry — the None scope "
        "changed, which would also flush every legacy entry on deploy"
    )


def test_c2_scope_token_unchanged_without_consumer():
    """Static guard on backward compat: _scope_token with no allowed_domains must
    equal the historical domain-only token, so pre-fix entries + the hardcoded
    '__all__' fixtures in test_semantic_cache keep matching."""
    assert semantic_cache._scope_token(None) == "__all__"
    assert semantic_cache._scope_token(["b", "a"]) == "a,b"
    assert semantic_cache._scope_token(None, None) == "__all__"
    # With a consumer wall, the token gains a distinct, stable suffix.
    assert semantic_cache._scope_token(None, _FINANCE) == "__all__|allow=finance,general"
    assert semantic_cache._scope_token(None, _FINANCE) != semantic_cache._scope_token(None)
