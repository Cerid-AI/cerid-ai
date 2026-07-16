# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""CL-12 / Phase-0 divergence probes — the verifiability harness for the
ingestion / artifact / corpus systemic audit
(``docs/superpowers/plans/2026-07-15-remediation-program.md`` Phase 0,
``docs/superpowers/specs/2026-07-15-phase01-shared-contract.md`` §5).

These are **synthetic** preservation tests — no live stack. They wire real
service code (retention delete, the query-agent active-learning join, the
startup divergence probe) to in-memory store doubles and assert the four-store
/ cache / vector-visible-archived invariants the audit proved were violated.

Three of them (``four_store_residual``, ``cache_residual``,
``vector_visible_archived``) are designed to fail **RED against today's code**
for the RIGHT reason (residual present / cache survives / archived not dropped)
and to go GREEN only after the Phase-1 CL-2 + CL-14 fixes route these callers
through the content-lifecycle coordinator. The fourth (``two_store``) exercises
the ``/health`` divergence-probe logic directly.

IMPORTANT: this module never imports ``app.services.content_lifecycle`` — that
coordinator is being written in parallel and does not exist yet. Every probe
drives an EXISTING entry point and inspects stores DIRECTLY.
"""
from __future__ import annotations

import json

import pytest

import config
from tests.helpers.fake_neo4j import _FakeNeo4jDriver

# Reuse the faithful in-memory Chroma collection double (real metadata tracking,
# real int count) from the O.1 preservation gate rather than replicate it.
from tests.integration.test_o1_ingest_atomicity_preservation import (
    _make_tracked_collection,
)

# Whole module is a preservation gate (matches test_o1 pattern).
pytestmark = pytest.mark.preservation


# ---------------------------------------------------------------------------
# In-memory Chroma client double (maps collection name -> tracked collection)
# ---------------------------------------------------------------------------

class _FakeChroma:
    """Minimal Chroma client over faithful tracked collections."""

    def __init__(self, collections: dict[str, object] | None = None):
        self._collections: dict[str, object] = dict(collections or {})

    def get_or_create_collection(self, name: str, **kwargs):
        coll = self._collections.get(name)
        if coll is None:
            coll = _make_tracked_collection()
            self._collections[name] = coll
        return coll

    def get_collection(self, name: str, **kwargs):
        return self._collections[name]

    def list_collections(self):
        out = []
        for name, coll in self._collections.items():
            # The tracked-collection doubles are MagicMocks, so setattr always
            # succeeds — no defensive catch needed (a real Chroma collection
            # object is never used here).
            setattr(coll, "name", name)
            out.append(coll)
        return out


@pytest.fixture
def reset_retrieval_indices():
    """Isolate the module-level BM25/SPLADE index caches.

    The production removal coordinator drives BM25/SPLADE through the
    module-level ``get_index(domain)`` cache, so seed + delete + assert must
    all hit the SAME instance. We snapshot and restore the caches so a probe
    that repoints the data dir at ``tmp_path`` never leaks a stale index into
    another test.
    """
    from core.retrieval import bm25, sparse_index

    bm25_snapshot = dict(bm25._indexes)
    sparse_snapshot = dict(sparse_index._indexes)
    bm25._indexes.clear()
    sparse_index._indexes.clear()
    try:
        yield
    finally:
        bm25._indexes.clear()
        bm25._indexes.update(bm25_snapshot)
        sparse_index._indexes.clear()
        sparse_index._indexes.update(sparse_snapshot)


def _seed_committed(collection, chunk_ids: list[str]) -> None:
    collection.add(
        ids=list(chunk_ids),
        documents=[f"doc for {c}" for c in chunk_ids],
        metadatas=[{"cerid_state": "committed"} for _ in chunk_ids],
    )


# ---------------------------------------------------------------------------
# Probe 1 — four-store residual (RED today: bm25/sparse orphaned on delete)
# ---------------------------------------------------------------------------

def test_probe1_four_store_residual(tmp_path, monkeypatch, reset_retrieval_indices):
    """After a delete-shaped caller runs, ZERO residual must remain for the
    deleted artifact's chunks across Chroma AND BM25 AND SPLADE.

    Driver: ``app.services.retention.apply_retention_plan`` — the verified
    delete path that removes the Neo4j node + Chroma chunks but NEVER calls
    ``remove_chunks`` on BM25/SPLADE (retention.py:51-120).

    RED today: BM25 (and SPLADE, when available) still hold the chunks.
    GREEN after Phase 1 routes retention through the coordinator, which fans
    the removal across every enumerated store participant.
    """
    import fakeredis

    from app.services.retention import apply_retention_plan
    from core.ingest.retention import RetentionDecision
    from core.retrieval import bm25, sparse_index

    domain = "coding"
    chunk_ids = ["cl12art1_chunk_0", "cl12art1_chunk_1"]
    texts = ["alpha bravo charlie keyword", "delta echo foxtrot keyword"]

    # Point the module-level indices at tmp_path so seed + coordinator-removal
    # + assert all resolve to the SAME get_index(domain) instance.
    monkeypatch.setattr(config, "BM25_DATA_DIR", str(tmp_path / "bm25"))
    monkeypatch.setattr(sparse_index, "SPARSE_DATA_DIR", str(tmp_path / "sparse"))

    # The coordinator busts the query caches as part of every hard delete, so it
    # resolves a redis handle. Wire a fake (as probe2 does) so this store-focused
    # probe exercises the removal without reaching for a live redis.
    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("utils.query_cache.get_redis", lambda: fake_redis)
    monkeypatch.setattr("app.deps.get_redis", lambda: fake_redis)

    # -- seed all four stores with the SAME chunk_ids ------------------------
    collection = _make_tracked_collection()
    _seed_committed(collection, chunk_ids)
    chroma = _FakeChroma({config.collection_name(domain): collection})
    monkeypatch.setattr("app.deps.get_chroma", lambda: chroma)

    assert bm25.is_available(), "bm25s must be installed for the BM25 residual arm"
    added = bm25.index_chunks(domain, chunk_ids, texts)
    assert added == len(chunk_ids)
    assert all(c in bm25.get_index(domain)._doc_id_set for c in chunk_ids), (
        "seed sanity: BM25 must hold the chunks before the delete"
    )

    sparse_available = sparse_index.is_available()
    if sparse_available:
        sparse_index.index_chunks(domain, chunk_ids, texts)

    driver = _FakeNeo4jDriver()
    driver.add_artifact("cl12art1", chunk_ids=chunk_ids, domain=domain)

    # -- drive the real delete path -----------------------------------------
    purged = apply_retention_plan(
        driver, RetentionDecision(source_id="src-1", purge=["cl12art1"], keep_count=0)
    )
    assert purged == 1
    assert "cl12art1" not in driver.nodes, "Neo4j node should be DETACH DELETEd"

    # -- residual across every store ----------------------------------------
    residual = {
        "chroma": len(collection.get(ids=chunk_ids)["ids"]),
        "bm25": sum(1 for c in chunk_ids if c in bm25.get_index(domain)._doc_id_set),
    }
    if sparse_available:
        residual["sparse"] = sum(
            1 for c in chunk_ids if c in sparse_index.get_index(domain)._docs
        )

    expected_zero = {k: 0 for k in residual}
    assert residual == expected_zero, (
        f"four-store residual after retention delete: {residual} "
        f"(RED reason: retention deletes Neo4j+Chroma but never routes "
        f"BM25/SPLADE through remove_chunks — chunks orphaned in the lexical "
        f"stores; GREEN once Phase-1 routes retention through the coordinator)"
    )


# ---------------------------------------------------------------------------
# Probe 2 — cache residual (RED today: delete/wipe busts neither cache)
# ---------------------------------------------------------------------------

def test_probe2_cache_residual_after_delete(tmp_path, monkeypatch):
    """After a delete/wipe, ZERO query-result cache keys citing the deleted
    artifact may survive — C1 flat (``qcache:*``) and C2 semantic
    (``semcache:*``).

    Driver: ``retention.apply_retention_plan`` again — it touches neither cache
    today (no ``utils.query_cache`` / ``semantic_cache`` reference in the file).

    RED today: both seeded keys survive.
    GREEN after Phase 1 funnels the delete through ``invalidate_query_caches``,
    which SCAN+DELETEs both namespaces via the app Redis singleton.
    """
    import fakeredis

    from app.services.retention import apply_retention_plan
    from core.ingest.retention import RetentionDecision

    domain = "coding"
    artifact_id = "cl12-cache-art"
    chunk_ids = ["cl12-cache-art_chunk_0"]

    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    # C1 flat-cache entry (key is a hash; payload cites the artifact) + C2
    # semantic-cache entry keyed by the artifact.
    fake_redis.setex(
        "qcache:deadbeefdeadbeefdeadbeefdeadbeef",
        300,
        json.dumps({"answer": "leaked secret", "sources": [artifact_id]}),
    )
    fake_redis.set(
        f"semcache:entry:{artifact_id}",
        json.dumps({"query": "secret?", "sources": [artifact_id]}),
    )
    assert fake_redis.keys("qcache:*") and fake_redis.keys("semcache:*"), (
        "seed sanity: both caches must hold an entry before the delete"
    )

    # Wire the fake redis into every getter the (post-fix) delete path uses.
    # C1 invalidate_all() resolves redis via utils.query_cache.get_redis; the
    # coordinator resolves its handle via app.deps.get_redis.
    monkeypatch.setattr("utils.query_cache.get_redis", lambda: fake_redis)
    monkeypatch.setattr("app.deps.get_redis", lambda: fake_redis)

    collection = _make_tracked_collection()
    _seed_committed(collection, chunk_ids)
    chroma = _FakeChroma({config.collection_name(domain): collection})
    monkeypatch.setattr("app.deps.get_chroma", lambda: chroma)

    driver = _FakeNeo4jDriver()
    driver.add_artifact(artifact_id, chunk_ids=chunk_ids, domain=domain)

    apply_retention_plan(
        driver, RetentionDecision(source_id="src-1", purge=[artifact_id], keep_count=0)
    )

    survivors = fake_redis.keys("qcache:*") + fake_redis.keys("semcache:*")
    assert survivors == [], (
        f"query-result cache keys survived a delete/wipe: {survivors} "
        f"(RED reason: retention busts neither C1 qcache nor C2 semcache — "
        f"deleted content servable from cache for up to a full TTL; GREEN once "
        f"Phase-1 routes the delete through invalidate_query_caches)"
    )


# ---------------------------------------------------------------------------
# Probe 3 — vector-visible-archived (the AF-001 probe; RED today)
# ---------------------------------------------------------------------------

def test_probe3_vector_visible_archived_dropped():
    """An ``archived`` artifact must NOT survive the query-agent's
    post-retrieval active-learning join (the vector answer arm).

    Driver: ``core.agents.query_agent._apply_active_learning_signals`` directly
    (query_agent.py:1474) — the existing Neo4j post-retrieval join that already
    DROPS ``flag_reason``-carrying chunks.

    RED today (AF-001): the join RETURN reads only ``id/weight/flag`` — it never
    reads ``archived``, so the archived chunk is NOT dropped and leaks as RAG
    evidence. GREEN after Phase 1 adds the ``archived`` term + drop.
    """
    from core.agents.query_agent import _apply_active_learning_signals

    driver = _FakeNeo4jDriver()
    driver.add_artifact("art-archived", chunk_ids=["ca0"], domain="coding", archived=True)
    driver.add_artifact("art-live", chunk_ids=["cl0"], domain="coding", archived=False)

    results = [
        {"artifact_id": "art-archived", "chunk_id": "ca0", "content": "secret", "relevance": 0.9},
        {"artifact_id": "art-live", "chunk_id": "cl0", "content": "public", "relevance": 0.8},
    ]

    filtered = _apply_active_learning_signals(results, driver)
    surviving = {r["artifact_id"] for r in filtered}

    assert "art-archived" not in surviving, (
        f"archived artifact still surfaced on the vector arm (survivors={surviving}) "
        f"(RED reason: _apply_active_learning_signals join does not read/drop "
        f"archived — AF-001; GREEN once Phase-1 adds coalesce(a.archived,false) "
        f"to the RETURN + drops archived chunks)"
    )


def test_probe3b_non_archived_survives():
    """Belt-and-suspenders: a NON-archived chunk must survive the join
    (guards against an over-broad archived filter dropping live content).
    Passes today and after Phase 1 — an OFF-path byte-identity anchor.
    """
    from core.agents.query_agent import _apply_active_learning_signals

    driver = _FakeNeo4jDriver()
    driver.add_artifact("art-live", chunk_ids=["cl0"], domain="coding", archived=False)

    results = [
        {"artifact_id": "art-live", "chunk_id": "cl0", "content": "public", "relevance": 0.8},
    ]

    filtered = _apply_active_learning_signals(results, driver)
    assert {r["artifact_id"] for r in filtered} == {"art-live"}


# ---------------------------------------------------------------------------
# Probe 4 — two-store divergence metric (/health probe logic)
# ---------------------------------------------------------------------------

def test_probe4_two_store_divergence_metric():
    """``_probe_divergence`` must count a Neo4j ``chunk_count`` vs committed
    Chroma-count mismatch, and must flag an archived artifact whose chunks are
    still vector-visible. Exercises the warn-only ``/health`` probe logic.
    """
    from app.startup.invariants import _probe_divergence

    domain = "coding"
    collection = _make_tracked_collection()
    # consistent: node says 2, Chroma holds 2
    _seed_committed(collection, ["c-ok-0", "c-ok-1"])
    # inconsistent: node says 3, Chroma holds 1
    _seed_committed(collection, ["c-bad-0"])
    # archived-but-visible: node says 1, Chroma holds 1
    _seed_committed(collection, ["c-arch-0"])
    chroma = _FakeChroma({config.collection_name(domain): collection})

    driver = _FakeNeo4jDriver()
    driver.add_artifact("art-ok", chunk_ids=["c-ok-0", "c-ok-1"], domain=domain, chunk_count=2)
    driver.add_artifact(
        "art-bad", chunk_ids=["c-bad-0", "c-bad-1", "c-bad-2"], domain=domain, chunk_count=3
    )
    driver.add_artifact("art-arch", chunk_ids=["c-arch-0"], domain=domain, chunk_count=1, archived=True)

    out = _probe_divergence(chroma, driver)

    assert out["two_store_residual"] == 1, (
        f"expected exactly 1 chunk_count vs Chroma mismatch (art-bad), got {out}"
    )
    assert out["vector_visible_archived"] == 1, (
        f"expected exactly 1 archived-but-vector-visible artifact (art-arch), got {out}"
    )


def test_probe4_consistent_corpus_has_zero_divergence():
    """A fully consistent corpus must report zero divergence on both axes —
    the metric must not false-positive on healthy artifacts.
    """
    from app.startup.invariants import _probe_divergence

    domain = "coding"
    collection = _make_tracked_collection()
    _seed_committed(collection, ["c-a-0", "c-a-1"])
    _seed_committed(collection, ["c-b-0"])
    chroma = _FakeChroma({config.collection_name(domain): collection})

    driver = _FakeNeo4jDriver()
    driver.add_artifact("art-a", chunk_ids=["c-a-0", "c-a-1"], domain=domain, chunk_count=2)
    driver.add_artifact("art-b", chunk_ids=["c-b-0"], domain=domain, chunk_count=1)

    out = _probe_divergence(chroma, driver)
    assert out == {"two_store_residual": 0, "vector_visible_archived": 0}
