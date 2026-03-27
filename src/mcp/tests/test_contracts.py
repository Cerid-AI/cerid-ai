# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Verify all contract ABCs are importable and abstract."""
from abc import ABC

import pytest


def test_vector_store_is_abstract():
    from core.contracts.stores import VectorStore
    assert issubclass(VectorStore, ABC)
    with pytest.raises(TypeError):
        VectorStore()


def test_graph_store_is_abstract():
    from core.contracts.stores import GraphStore
    assert issubclass(GraphStore, ABC)
    with pytest.raises(TypeError):
        GraphStore()


def test_llm_client_is_abstract():
    from core.contracts.llm import LLMClient
    assert issubclass(LLMClient, ABC)
    with pytest.raises(TypeError):
        LLMClient()


def test_cache_store_is_abstract():
    from core.contracts.cache import CacheStore
    assert issubclass(CacheStore, ABC)
    with pytest.raises(TypeError):
        CacheStore()


def test_embedding_function_is_abstract():
    from core.contracts.embedding import EmbeddingFunction
    assert issubclass(EmbeddingFunction, ABC)
    with pytest.raises(TypeError):
        EmbeddingFunction()


def test_audit_log_is_abstract():
    from core.contracts.audit import AuditLog
    assert issubclass(AuditLog, ABC)
    with pytest.raises(TypeError):
        AuditLog()


def test_dataclasses_importable():
    from core.contracts.audit import AuditEvent
    from core.contracts.llm import LLMResponse
    from core.contracts.stores import ArtifactNode, SearchResult

    # Verify they're constructable
    sr = SearchResult(artifact_id="a1", chunk_id="c1", content="text", metadata={}, distance=0.5)
    assert sr.artifact_id == "a1"

    an = ArtifactNode(id="a1", filename="f.pdf", domain="general", sub_category="notes",
                      tags=["test"], summary="A doc", quality_score=0.8)
    assert an.domain == "general"

    lr = LLMResponse(content="hello", model="test-model")
    assert lr.usage is None

    ae = AuditEvent(action="query", actor="user1", resource="test")
    assert ae.timestamp is None
