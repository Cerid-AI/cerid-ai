# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

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
