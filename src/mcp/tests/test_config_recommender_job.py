# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ConfigRecommenderJob + recommendation registry (C3.2)."""

from __future__ import annotations

import pytest

from app.processor.jobs.config_recommender import (
    _REDIS_HASH_KEY,
    _read_flag_state,
    run_recommender_sync,
)
from core.config import recommendations as rec_module
from core.config.recommendations import (
    RECOMMENDATIONS,
    CorpusStats,
    evaluate,
)

# ---------------------------------------------------------------------------
# Helpers — fake Neo4j + Redis that record interactions for assertions.
# ---------------------------------------------------------------------------

class _FakeNeo4jSession:
    def __init__(self, count: int) -> None:
        self._count = count

    def run(self, _cypher, **_kwargs):
        return _FakeNeo4jResult(self._count)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FakeNeo4jResult:
    def __init__(self, count: int) -> None:
        self._count = count

    def single(self):
        return {"n": self._count}


class _FakeNeo4jDriver:
    def __init__(self, count: int = 0) -> None:
        self._count = count

    def session(self):
        return _FakeNeo4jSession(self._count)


class _FakePipeline:
    def __init__(self, sink: dict) -> None:
        self._sink = sink

    def delete(self, key):
        self._sink.pop(key, None)
        return self

    def hset(self, key, field, value):
        self._sink.setdefault(key, {})[field] = value
        return self

    def execute(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict = {}

    def pipeline(self):
        return _FakePipeline(self.store)


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

def test_registry_has_expected_recommendations():
    ids = {spec.id for spec in RECOMMENDATIONS}
    assert ids == {
        "sparse_retrieval",
        "hype_indexing",
        "parent_child_retrieval",
        "rrf_fusion",
        # v0.93.5 — second user of the recommender engine, fires on
        # conversation length rather than artifact count.
        "chat_virtualization",
    }


def test_chat_virtualization_fires_on_long_conversation():
    """The new C3.2 follow-on user of the recommender engine."""
    from core.config.recommendations import CorpusStats, evaluate

    # Short conversation: no fire.
    stats_short = CorpusStats(
        artifact_count=10,
        flags_enabled=frozenset(),
        longest_conversation_length=50,
    )
    ids_short = {spec.id for spec, _ in evaluate(stats_short)}
    assert "chat_virtualization" not in ids_short

    # Long conversation: fires regardless of artifact count.
    stats_long = CorpusStats(
        artifact_count=10,
        flags_enabled=frozenset(),
        longest_conversation_length=250,
    )
    ids_long = {spec.id for spec, _ in evaluate(stats_long)}
    assert "chat_virtualization" in ids_long

    # Already enabled: no fire even at long length.
    stats_on = CorpusStats(
        artifact_count=10,
        flags_enabled=frozenset({"ENABLE_CHAT_VIRTUALIZATION"}),
        longest_conversation_length=500,
    )
    ids_on = {spec.id for spec, _ in evaluate(stats_on)}
    assert "chat_virtualization" not in ids_on


def test_corpus_below_threshold_fires_nothing():
    stats = CorpusStats(artifact_count=50, flags_enabled=frozenset())
    assert evaluate(stats) == []


def test_corpus_at_threshold_fires_sparse(monkeypatch):
    monkeypatch.setattr(rec_module, "_THRESHOLD_SPARSE", 100)
    stats = CorpusStats(artifact_count=100, flags_enabled=frozenset())
    hits = evaluate(stats)
    ids = {spec.id for spec, _ in hits}
    assert "sparse_retrieval" in ids


def test_flag_already_on_skips_recommendation():
    stats = CorpusStats(
        artifact_count=500,
        flags_enabled=frozenset({"RETRIEVAL_SPARSE_ENABLED"}),
    )
    ids = {spec.id for spec, _ in evaluate(stats)}
    assert "sparse_retrieval" not in ids


def test_reason_template_substitutes_count():
    stats = CorpusStats(artifact_count=250, flags_enabled=frozenset())
    hits = evaluate(stats)
    for _, reason in hits:
        if "250" in reason:
            break
    else:
        pytest.fail("count substitution missing from reason template")


# ---------------------------------------------------------------------------
# Flag-state reader
# ---------------------------------------------------------------------------

def test_read_flag_state_default_is_empty(monkeypatch):
    for var in (
        "RETRIEVAL_SPARSE_ENABLED",
        "RETRIEVAL_HYPE_ENABLED",
        "PARENT_CHILD_ENABLED",
        "HYBRID_FUSION_MODE",
    ):
        monkeypatch.delenv(var, raising=False)
    on = _read_flag_state()
    assert on == frozenset()


def test_read_flag_state_picks_up_rrf_mode(monkeypatch):
    monkeypatch.setenv("HYBRID_FUSION_MODE", "rrf")
    on = _read_flag_state()
    assert "HYBRID_FUSION_MODE_ACTIVE" in on


def test_read_flag_state_truthy_values(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_SPARSE_ENABLED", "yes")
    monkeypatch.setenv("PARENT_CHILD_ENABLED", "1")
    on = _read_flag_state()
    assert "RETRIEVAL_SPARSE_ENABLED" in on
    assert "PARENT_CHILD_ENABLED" in on


# ---------------------------------------------------------------------------
# End-to-end recommender pass
# ---------------------------------------------------------------------------

def test_recommender_writes_redis_hash_when_threshold_crossed(monkeypatch):
    monkeypatch.delenv("RETRIEVAL_SPARSE_ENABLED", raising=False)
    monkeypatch.delenv("RETRIEVAL_HYPE_ENABLED", raising=False)
    monkeypatch.delenv("PARENT_CHILD_ENABLED", raising=False)
    monkeypatch.delenv("HYBRID_FUSION_MODE", raising=False)

    redis = _FakeRedis()
    driver = _FakeNeo4jDriver(count=150)

    meta = run_recommender_sync(driver, redis)
    assert meta["corpus_size"] == 150
    # All three "@100" recommendations fire; RRF stays parked.
    written = redis.store.get(_REDIS_HASH_KEY, {})
    assert "sparse_retrieval" in written
    assert "hype_indexing" in written
    assert "parent_child_retrieval" in written
    assert "rrf_fusion" not in written


def test_recommender_clears_stale_entries(monkeypatch):
    """A second pass with all flags ON must wipe the hash.

    Idempotency property — the pipeline DELETE + re-HSET pattern is
    the contract that lets the /health endpoint trust the hash.
    """
    monkeypatch.delenv("RETRIEVAL_SPARSE_ENABLED", raising=False)
    monkeypatch.delenv("HYBRID_FUSION_MODE", raising=False)

    redis = _FakeRedis()
    driver = _FakeNeo4jDriver(count=600)

    # First pass — flags off, every rec fires.
    run_recommender_sync(driver, redis)
    assert redis.store.get(_REDIS_HASH_KEY)

    # Second pass — every flag on, hash should be empty.
    monkeypatch.setenv("RETRIEVAL_SPARSE_ENABLED", "true")
    monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")
    monkeypatch.setenv("PARENT_CHILD_ENABLED", "true")
    monkeypatch.setenv("HYBRID_FUSION_MODE", "tri_rrf")
    run_recommender_sync(driver, redis)
    # Pipeline DELETE then no HSET ⇒ key absent OR empty.
    assert not redis.store.get(_REDIS_HASH_KEY)


def test_recommender_survives_neo4j_failure():
    """A driver that raises on .session() must yield a zero-corpus pass."""

    class _FailingDriver:
        def session(self):
            raise RuntimeError("boom")

    redis = _FakeRedis()
    meta = run_recommender_sync(_FailingDriver(), redis)
    assert meta["corpus_size"] == 0
    assert meta["recommendations_written"] == 0


def test_recommender_handles_missing_redis():
    """A None redis client must not raise (lightweight / startup mode)."""
    driver = _FakeNeo4jDriver(count=200)
    meta = run_recommender_sync(driver, None)
    assert meta["recommendations_written"] == 0
