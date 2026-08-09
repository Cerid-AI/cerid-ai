# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for concrete store implementations."""
import json
from unittest.mock import MagicMock, patch

import pytest

from core.contracts.audit import AuditEvent, AuditLog
from core.contracts.cache import CacheStore
from core.contracts.llm import LLMClient, LLMResponse
from core.contracts.stores import GraphStore, SearchResult, VectorStore

# --- Compliance tests: verify each store implements its contract ---

def test_chroma_store_implements_vector_store():
    from app.stores.chroma_store import ChromaVectorStore
    assert issubclass(ChromaVectorStore, VectorStore)


def test_neo4j_store_implements_graph_store():
    from app.stores.neo4j_store import Neo4jGraphStore
    assert issubclass(Neo4jGraphStore, GraphStore)


def test_redis_cache_implements_cache_store():
    from app.stores.redis_cache import RedisCacheStore
    assert issubclass(RedisCacheStore, CacheStore)


def test_redis_audit_implements_audit_log():
    from app.stores.redis_audit import RedisAuditLog
    assert issubclass(RedisAuditLog, AuditLog)


def test_llm_client_implements_contract():
    from app.stores.llm_clients import OpenRouterLLMClient
    assert issubclass(OpenRouterLLMClient, LLMClient)


# --- Functional tests with mocks ---

@pytest.mark.asyncio
async def test_chroma_store_search_returns_search_results():
    from app.stores.chroma_store import ChromaVectorStore

    mock_collection = MagicMock()
    mock_collection.query.return_value = {
        "ids": [["chunk-1", "chunk-2"]],
        "documents": [["doc 1 text", "doc 2 text"]],
        "metadatas": [[{"artifact_id": "a1"}, {"artifact_id": "a2"}]],
        "distances": [[0.1, 0.3]],
    }
    store = ChromaVectorStore(mock_collection)
    results = await store.search([0.1, 0.2, 0.3], top_k=2)
    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    assert results[0].artifact_id == "a1"
    assert results[0].distance == 0.1


@pytest.mark.asyncio
async def test_chroma_store_count():
    from app.stores.chroma_store import ChromaVectorStore

    mock_collection = MagicMock()
    mock_collection.count.return_value = 42
    store = ChromaVectorStore(mock_collection)
    assert await store.count() == 42


@pytest.mark.asyncio
async def test_redis_cache_get_set_delete():
    from app.stores.redis_cache import RedisCacheStore

    mock_redis = MagicMock()
    mock_redis.get.return_value = b"cached-value"
    store = RedisCacheStore(mock_redis)

    val = await store.get("key")
    assert val == "cached-value"
    mock_redis.get.assert_called_with("key")

    await store.set("key", "value", ttl_seconds=60)
    mock_redis.setex.assert_called_with("key", 60, "value")

    await store.delete("key")
    mock_redis.delete.assert_called_with("key")


@pytest.mark.asyncio
async def test_redis_audit_record_and_query():
    from app.stores.redis_audit import RedisAuditLog

    mock_redis = MagicMock()
    store = RedisAuditLog(mock_redis)

    event = AuditEvent(action="query", actor="user1", resource="kb")
    await store.record(event)
    mock_redis.lpush.assert_called_once()

    stored = mock_redis.lpush.call_args[0][1]
    data = json.loads(stored)
    assert data["action"] == "query"
    assert data["actor"] == "user1"


@pytest.mark.asyncio
async def test_openrouter_llm_client_delegates():
    from app.stores.llm_clients import OpenRouterLLMClient

    with patch("core.utils.llm_client.call_llm") as mock_call:
        mock_call.return_value = "hello"
        client = OpenRouterLLMClient()
        resp = await client.call([{"role": "user", "content": "hi"}])
        assert isinstance(resp, LLMResponse)
        assert resp.content == "hello"


# --- Neo4jGraphStore row mapping (2026-07-12 KeyError 'artifact_id' regression) ---
#
# The db/neo4j layer returns row dicts keyed "id"; the adapter used to
# index r["artifact_id"] and raised KeyError on every graph lookup in the
# query path (swallowed twice per request as
# "swallowed KeyError in core.agents.query_agent: 'artifact_id'").

_DB_ROW = {
    "id": "art-1",
    "filename": "notes.md",
    "domain": "general",
    "summary": "A summary.",
    "keywords": "[]",
    "chunk_ids": "[]",
    "chunk_count": 2,
}


@pytest.mark.asyncio
async def test_neo4j_store_get_artifact_accepts_db_shaped_row():
    from app.stores.neo4j_store import Neo4jGraphStore

    store = Neo4jGraphStore(MagicMock())
    with patch("app.db.neo4j.artifacts.get_artifact", return_value=dict(_DB_ROW)):
        node = await store.get_artifact("art-1")

    assert node is not None
    assert node.id == "art-1"
    assert node.summary == "A summary."
    # Missing quality data reads as the neutral 0.5 (multiplier 1.0),
    # never as "worst quality" 0.0.
    assert node.quality_score == 0.5


@pytest.mark.asyncio
async def test_neo4j_store_get_quality_and_summaries_uses_real_batch_query():
    """Neo4jGraphStore.get_quality_and_summaries must delegate to the real
    UNWIND batch query (app.db.neo4j.artifacts.get_quality_and_summaries),
    not the ABC default's per-artifact get_artifacts_batch fan-out."""
    from app.stores.neo4j_store import Neo4jGraphStore

    store = Neo4jGraphStore(MagicMock())
    with patch(
        "app.db.neo4j.artifacts.get_quality_and_summaries",
        return_value=({"art-1": 0.9}, {"art-1": "A summary."}),
    ) as mock_batch:
        scores, summaries = await store.get_quality_and_summaries(["art-1", "art-2"])

    mock_batch.assert_called_once_with(store._driver, ["art-1", "art-2"])
    assert scores == {"art-1": 0.9}
    assert summaries == {"art-1": "A summary."}


@pytest.mark.asyncio
async def test_neo4j_store_get_related_accepts_db_shaped_rows_and_skips_idless():
    from app.stores.neo4j_store import Neo4jGraphStore

    rows = [
        dict(_DB_ROW),
        {**_DB_ROW, "id": "art-2"},
        {"filename": "orphan-row.md"},  # no id under either key → skipped
    ]
    store = Neo4jGraphStore(MagicMock())
    with patch("app.db.neo4j.find_related_artifacts", return_value=rows):
        nodes = await store.get_related(["seed-1"])

    assert [n.id for n in nodes] == ["art-1", "art-2"]


@pytest.mark.asyncio
async def test_neo4j_store_get_artifact_prefers_artifact_id_key():
    from app.stores.neo4j_store import Neo4jGraphStore

    row = {**_DB_ROW, "artifact_id": "art-canonical"}
    store = Neo4jGraphStore(MagicMock())
    with patch("app.db.neo4j.artifacts.get_artifact", return_value=row):
        node = await store.get_artifact("art-canonical")

    assert node is not None
    assert node.id == "art-canonical"


@pytest.mark.asyncio
async def test_neo4j_store_batch_routes_through_get_quality_and_summaries():
    """End-to-end regression at the query-agent consumption site (Phase 2.1 fix).

    _apply_quality_and_summaries must route through Neo4jGraphStore's real,
    single-round-trip ``get_quality_and_summaries`` override — not the
    ``get_artifacts_batch`` N-query fan-out that always defaulted every
    artifact to 0.5 — so a real stored score flows through as a genuine
    multiplier while a never-curated artifact still gets the neutral default.
    """
    from app.stores.neo4j_store import Neo4jGraphStore
    from core.agents.query_agent import _apply_quality_and_summaries

    store = Neo4jGraphStore(MagicMock())
    results = [
        {"artifact_id": "art-1", "chunk_id": "c1", "relevance": 0.8, "content": "x"},
        {"artifact_id": "art-2", "chunk_id": "c2", "relevance": 0.8, "content": "y"},
        {"chunk_id": "external-1", "relevance": 0.5, "content": "z"},  # no artifact_id
    ]
    scores = {"art-1": 1.0}  # art-2 deliberately absent → neutral 0.5 default
    summaries = {"art-1": "A summary."}
    with patch(
        "app.db.neo4j.artifacts.get_quality_and_summaries",
        return_value=(scores, summaries),
    ) as mock_batch:
        out = await _apply_quality_and_summaries(
            [dict(r) for r in results], graph_store=store,
        )

    mock_batch.assert_called_once()
    assert out[0]["relevance"] == pytest.approx(0.8 * 1.2)  # quality 1.0 → 1.2x
    assert out[1]["relevance"] == pytest.approx(0.8 * 1.0)  # unscored → neutral 0.5
    assert out[2]["relevance"] == pytest.approx(0.5)  # no artifact_id, untouched
    assert out[0]["summary"] == "A summary."
