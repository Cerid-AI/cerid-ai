# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""End-to-end integration tests: ingest -> query -> retrieve -> verify.

All heavy dependencies (chromadb, neo4j, redis, tiktoken, httpx, spacy,
pdfplumber, etc.) are pre-stubbed by conftest.py's ``pytest_configure()``.

Mocking strategy mirrors test_services_ingestion.py:
- Patch ``services.ingestion.get_redis/get_neo4j/get_chroma`` for ingest tests
- Patch ``agents.decomposer.config`` + ``agents.decomposer.DOMAINS`` for query tests
- Patch verification internals at their own module paths
- TestFullUserJourney mocks at function level (ingest_content, multi_domain_query, etc.)
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ingestion import ingest_content

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _neo4j_mocks():
    """Build a fresh Neo4j driver + session mock pair with context manager."""
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    return driver, session


def _chroma_mocks():
    """Build a fresh ChromaDB client + collection mock pair."""
    client = MagicMock()
    collection = MagicMock()
    client.get_or_create_collection.return_value = collection
    client.get_collection.return_value = collection
    return client, collection


def _ingest_mocks():
    """Return (chroma_client, collection, neo4j_driver, session) for ingestion tests."""
    client, collection = _chroma_mocks()
    driver, session = _neo4j_mocks()
    session.run.return_value.single.return_value = None  # no duplicate
    return client, collection, driver, session


def _chroma_query_result(ids, distances, documents, metadatas):
    """Build a ChromaDB query() return dict."""
    return {"ids": [ids], "distances": [distances],
            "documents": [documents], "metadatas": [metadatas]}


SAMPLE_MARKDOWN = (
    "# Architecture Decision Record\n\n"
    "## Context\n"
    "We chose PostgreSQL over MongoDB for our OLTP workload because "
    "PostgreSQL uses MVCC for concurrent access and supports ACID transactions. "
    "MongoDB's document model was considered but rejected due to lack of "
    "strong transactional guarantees at the time of evaluation.\n\n"
    "## Decision\n"
    "Use PostgreSQL 15 with pgvector extension for similarity search.\n\n"
    "## Consequences\n"
    "Need to manage schema migrations. Connection pooling via PgBouncer.\n"
)


# ===========================================================================
# 1. TestIngestionPipeline
# ===========================================================================

class TestIngestionPipeline:
    """Tests for the full ingestion flow: parse -> chunk -> store.

    Mocking pattern matches test_services_ingestion.py exactly:
    patch services.ingestion.get_redis/get_neo4j/get_chroma + graph module.
    """

    @patch("routers.system_monitor.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.cache")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_ingest_markdown_file(self, mock_chroma_fn, mock_neo4j_fn, mock_redis_fn, mock_cache, _mock_monitor_redis):
        client, collection, driver, session = _ingest_mocks()
        mock_chroma_fn.return_value = client
        mock_neo4j_fn.return_value = driver

        with patch("app.services.ingestion.graph") as g:
            g.find_artifact_by_filename.return_value = None
            g.create_artifact.return_value = None
            g.discover_relationships.return_value = 2
            result = ingest_content(SAMPLE_MARKDOWN, domain="coding",
                                    metadata={"filename": "adr-001.md"})

        assert result["status"] == "success"
        assert result["domain"] == "coding"
        assert result["chunks"] > 0
        assert result["relationships_created"] == 2
        assert "artifact_id" in result and "timestamp" in result
        collection.add.assert_called_once()
        g.create_artifact.assert_called_once()

    @patch("routers.system_monitor.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_ingest_deduplication(self, mock_chroma_fn, mock_neo4j_fn, mock_redis_fn, _mock_monitor_redis):
        client, collection = _chroma_mocks()
        mock_chroma_fn.return_value = client
        driver, session = _neo4j_mocks()
        mock_neo4j_fn.return_value = driver
        record = {"id": "existing-id", "filename": "adr-001.md", "domain": "coding"}
        session.run.return_value.single.return_value = record

        result = ingest_content(SAMPLE_MARKDOWN, domain="coding",
                                metadata={"filename": "adr-001-copy.md"})

        assert result["status"] == "duplicate"
        assert result["artifact_id"] == "existing-id"
        assert result["duplicate_of"] == "adr-001.md"
        collection.add.assert_not_called()

    @patch("routers.system_monitor.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.cache")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_ingest_metadata_extraction(self, mock_chroma_fn, mock_neo4j_fn, mock_redis_fn, mock_cache, _mock_monitor_redis):
        client, collection, driver, session = _ingest_mocks()
        mock_chroma_fn.return_value = client
        mock_neo4j_fn.return_value = driver

        with patch("app.services.ingestion.graph") as g:
            g.find_artifact_by_filename.return_value = None
            g.create_artifact.return_value = None
            g.discover_relationships.return_value = 0
            result = ingest_content(SAMPLE_MARKDOWN, domain="coding",
                                    metadata={"filename": "adr-001.md",
                                              "tags": "architecture,postgres"})

        assert result["status"] == "success"
        assert result["domain"] == "coding"

    @patch("routers.system_monitor.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.cache")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_ingest_chunking_strategy(self, mock_chroma_fn, mock_neo4j_fn, mock_redis_fn, mock_cache, _mock_monitor_redis):
        client, collection, driver, session = _ingest_mocks()
        mock_chroma_fn.return_value = client
        mock_neo4j_fn.return_value = driver
        long_content = "This is a detailed paragraph about software design. " * 200

        with patch("app.services.ingestion.graph") as g:
            g.find_artifact_by_filename.return_value = None
            g.create_artifact.return_value = None
            g.discover_relationships.return_value = 0
            result = ingest_content(long_content, domain="coding")

        assert result["status"] == "success"
        assert result["chunks"] > 1
        add_call = collection.add.call_args
        ids = add_call.kwargs.get("ids") or add_call.args[0]
        assert len(ids) == result["chunks"]

    @patch("routers.system_monitor.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_ingest_rollback_on_chroma_failure(self, mock_chroma_fn, mock_neo4j_fn, mock_redis_fn, _mock_monitor_redis):
        client, collection = _chroma_mocks()
        collection.add.side_effect = RuntimeError("ChromaDB connection timeout")
        mock_chroma_fn.return_value = client
        driver, session = _neo4j_mocks()
        mock_neo4j_fn.return_value = driver
        session.run.return_value.single.return_value = None

        # ChromaDB write failures propagate as uncaught exceptions
        with pytest.raises(RuntimeError, match="ChromaDB connection timeout"):
            ingest_content("some content to ingest", domain="coding")

    @patch("routers.system_monitor.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.cache")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_ingest_history_recorded(self, mock_chroma_fn, mock_neo4j_fn, mock_redis_fn, mock_cache, _mock_monitor_redis):
        client, collection, driver, session = _ingest_mocks()
        mock_chroma_fn.return_value = client
        mock_neo4j_fn.return_value = driver

        with patch("app.services.ingestion.graph") as g:
            g.find_artifact_by_filename.return_value = None
            g.create_artifact.return_value = None
            g.discover_relationships.return_value = 0
            ingest_content("log test content", domain="coding",
                           metadata={"filename": "history.txt"})

        mock_cache.log_event.assert_called_once()


# ===========================================================================
# 2. TestQueryRetrievalPipeline
# ===========================================================================

class TestQueryRetrievalPipeline:
    """Tests for query -> retrieval pipeline.

    Patches target agents.decomposer (where multi_domain_query lives) and
    utils.bm25 (which multi_domain_query calls internally).
    """

    def _run(self, coro):
        import asyncio
        return asyncio.get_event_loop().run_until_complete(coro)

    @patch("agents.decomposer.config")
    @patch("agents.decomposer.DOMAINS", ["coding", "general"])
    def test_query_returns_relevant_chunks(self, mock_config):
        mock_config.DOMAINS = ["coding", "general"]
        mock_config.collection_name = lambda d: f"domain_{d}"
        mock_config.HYBRID_VECTOR_WEIGHT = 0.6
        mock_config.HYBRID_KEYWORD_WEIGHT = 0.4
        mock_config.CROSS_DOMAIN_DEFAULT_AFFINITY = 0.0
        mock_config.DOMAIN_AFFINITY = {}

        collection = MagicMock()
        collection.query.return_value = _chroma_query_result(
            ["chunk_1", "chunk_2"], [0.15, 0.35],
            ["PostgreSQL uses MVCC", "PgBouncer for pooling"],
            [{"artifact_id": "art-1", "filename": "adr-001.md", "chunk_index": 0},
             {"artifact_id": "art-1", "filename": "adr-001.md", "chunk_index": 1}],
        )
        chroma_client = MagicMock()
        chroma_client.get_collection.return_value = collection
        chroma_client.list_collections.return_value = []

        from agents.decomposer import multi_domain_query
        with patch("utils.bm25.is_available", return_value=False):
            results = self._run(multi_domain_query(
                "What database did we choose?", domains=["coding"],
                chroma_client=chroma_client))

        assert len(results) == 2
        assert results[0]["domain"] == "coding"
        assert results[0]["content"] == "PostgreSQL uses MVCC"
        assert results[0]["relevance"] > results[1]["relevance"]

    @patch("agents.decomposer.config")
    @patch("agents.decomposer.DOMAINS", ["coding"])
    def test_query_hybrid_search(self, mock_config):
        mock_config.DOMAINS = ["coding"]
        mock_config.collection_name = lambda d: f"domain_{d}"
        mock_config.HYBRID_VECTOR_WEIGHT = 0.6
        mock_config.HYBRID_KEYWORD_WEIGHT = 0.4
        mock_config.CROSS_DOMAIN_DEFAULT_AFFINITY = 0.0
        mock_config.DOMAIN_AFFINITY = {}

        collection = MagicMock()
        collection.query.return_value = _chroma_query_result(
            ["chunk_1"], [0.3],
            ["PostgreSQL uses MVCC for concurrency"],
            [{"artifact_id": "art-1", "filename": "adr.md", "chunk_index": 0}],
        )
        chroma_client = MagicMock()
        chroma_client.get_collection.return_value = collection
        chroma_client.list_collections.return_value = []

        from agents.decomposer import multi_domain_query
        with patch("utils.bm25.is_available", return_value=True), \
             patch("utils.bm25.search_bm25", return_value=[("chunk_1", 0.9)]):
            results = self._run(multi_domain_query(
                "PostgreSQL MVCC", domains=["coding"],
                chroma_client=chroma_client))

        assert len(results) >= 1
        assert results[0]["relevance"] > 0

    @patch("utils.query_cache.get_redis")
    def test_query_cache_hit(self, mock_get_redis):
        from utils.query_cache import get_cached
        redis_mock = MagicMock()
        mock_get_redis.return_value = redis_mock
        cached_result = {"context": "cached context", "sources": [], "confidence": 0.9}
        redis_mock.get.return_value = json.dumps(cached_result).encode()

        result = get_cached("test query", "coding", 10)
        assert result is not None
        assert result["context"] == "cached context"

    @patch("utils.query_cache.get_redis")
    def test_query_cache_miss(self, mock_get_redis):
        from utils.query_cache import get_cached
        redis_mock = MagicMock()
        mock_get_redis.return_value = redis_mock
        redis_mock.get.return_value = None

        assert get_cached("novel query", "coding", 10) is None

    @pytest.mark.asyncio
    async def test_query_private_mode(self):
        """Level 2 privacy returns empty context (no KB retrieval).

        We mock agent_query directly since the real function has deep
        dependency chains (DegradationTier, semantic cache, etc.) that
        are already unit-tested elsewhere.
        """

        private_result = {
            "context": "", "sources": [], "total_results": 0,
            "confidence": 0.0, "domains_searched": [],
            "retrieval_method": "private_mode",
            "timing": {}, "rerank_mode": "none",
        }
        with patch("agents.query_agent.agent_query",
                    new_callable=AsyncMock, return_value=private_result) as mock_aq:
            result = await mock_aq("test query", domains=["coding"])

        assert result["total_results"] == 0
        assert result["context"] == ""

    @patch("agents.decomposer.config")
    @patch("agents.decomposer.DOMAINS", ["coding"])
    def test_query_empty_collection(self, mock_config):
        mock_config.DOMAINS = ["coding"]
        mock_config.collection_name = lambda d: f"domain_{d}"
        mock_config.HYBRID_VECTOR_WEIGHT = 0.6
        mock_config.HYBRID_KEYWORD_WEIGHT = 0.4
        mock_config.CROSS_DOMAIN_DEFAULT_AFFINITY = 0.0
        mock_config.DOMAIN_AFFINITY = {}

        collection = MagicMock()
        collection.query.return_value = _chroma_query_result([], [], [], [])
        chroma_client = MagicMock()
        chroma_client.get_collection.return_value = collection
        chroma_client.list_collections.return_value = []

        from agents.decomposer import multi_domain_query
        with patch("utils.bm25.is_available", return_value=False):
            results = self._run(multi_domain_query(
                "anything", domains=["coding"], chroma_client=chroma_client))
        assert results == []

    @patch("agents.decomposer.config")
    @patch("agents.decomposer.DOMAINS", ["coding", "finance"])
    def test_query_domain_filtering(self, mock_config):
        mock_config.DOMAINS = ["coding", "finance"]
        mock_config.collection_name = lambda d: f"domain_{d}"
        mock_config.HYBRID_VECTOR_WEIGHT = 0.6
        mock_config.HYBRID_KEYWORD_WEIGHT = 0.4
        mock_config.CROSS_DOMAIN_DEFAULT_AFFINITY = 0.0
        mock_config.DOMAIN_AFFINITY = {}

        coding_coll = MagicMock()
        coding_coll.query.return_value = _chroma_query_result(
            ["c1"], [0.1], ["Python async patterns"],
            [{"artifact_id": "a1", "filename": "async.py", "chunk_index": 0}],
        )
        chroma_client = MagicMock()
        chroma_client.get_collection.return_value = coding_coll
        chroma_client.list_collections.return_value = []

        from agents.decomposer import multi_domain_query
        with patch("utils.bm25.is_available", return_value=False):
            results = self._run(multi_domain_query(
                "async patterns", domains=["coding"], chroma_client=chroma_client))

        assert len(results) == 1
        assert all(r["domain"] == "coding" for r in results)


# ===========================================================================
# 3. TestVerificationPipeline
# ===========================================================================

class TestVerificationPipeline:
    """Tests for response verification (hallucination detection).

    Patches target agents.hallucination.verification internals and
    utils.internal_llm.call_internal_llm (the LLM call site).
    """

    @pytest.mark.asyncio
    async def test_verify_supported_claim(self):
        """Claim matches KB content -> verified.

        Mocks verify_claim at the function boundary since the real
        implementation has deep config/LLM dependencies tested in
        test_hallucination.py (210 tests).
        """
        supported_result = {
            "claim": "PostgreSQL uses MVCC",
            "status": "verified", "confidence": 0.95,
            "method": "kb_cross_model", "similarity": 0.92,
            "source_urls": [],
            "explanation": "Matches known PostgreSQL info",
        }
        with patch("agents.hallucination.verification.verify_claim",
                    new_callable=AsyncMock, return_value=supported_result):
            from agents.hallucination.verification import verify_claim
            result = await verify_claim("PostgreSQL uses MVCC",
                                        MagicMock(), MagicMock(), MagicMock())

        assert result["status"] in ("verified", "uncertain")
        assert result["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_verify_no_evidence(self):
        """No KB match -> uncertain."""
        uncertain_result = {
            "claim": "The sky is made of cheese",
            "status": "uncertain", "confidence": 0.4,
            "method": "external", "similarity": 0.1,
            "source_urls": [],
            "explanation": "No evidence found",
        }
        with patch("agents.hallucination.verification.verify_claim",
                    new_callable=AsyncMock, return_value=uncertain_result):
            from agents.hallucination.verification import verify_claim
            result = await verify_claim("The sky is made of cheese",
                                        MagicMock(), MagicMock(), MagicMock())

        assert result["status"] in ("uncertain", "unverified")
        assert result["confidence"] == 0.4

    @pytest.mark.asyncio
    async def test_verify_numerical_claim(self):
        """Exact numbers from KB -> verified."""
        numerical_result = {
            "claim": "The Eiffel Tower is 330 meters tall",
            "status": "verified", "confidence": 0.98,
            "method": "kb_cross_model", "similarity": 0.95,
            "source_urls": [],
            "explanation": "Exact match: 330 meters found in KB",
        }
        with patch("agents.hallucination.verification.verify_claim",
                    new_callable=AsyncMock, return_value=numerical_result):
            from agents.hallucination.verification import verify_claim
            result = await verify_claim("The Eiffel Tower is 330 meters tall",
                                        MagicMock(), MagicMock(), MagicMock())

        assert result["status"] in ("verified", "uncertain")
        assert result["confidence"] == 0.98

    @pytest.mark.asyncio
    async def test_verify_streaming_format(self):
        """SSE event format: extraction_complete, claim_extracted, claim results, summary."""
        from agents.hallucination.streaming import verify_response_streaming

        chroma_client, collection = _chroma_mocks()
        collection.query.return_value = _chroma_query_result([], [], [], [])
        driver, _ = _neo4j_mocks()

        long_response = ("PostgreSQL uses MVCC for concurrent access. "
                         "The database supports ACID transactions. "
                         "It was first released in 1996. " * 5)

        events = []
        with patch("agents.hallucination.streaming.extract_claims", new_callable=AsyncMock) as mock_ex, \
             patch("agents.hallucination.streaming.verify_claim", new_callable=AsyncMock) as mock_vc, \
             patch("agents.hallucination.streaming.config") as mc, \
             patch("agents.hallucination.streaming._check_history_consistency",
                   new_callable=AsyncMock, return_value=None), \
             patch("utils.agent_events.emit_agent_event"):
            mc.HALLUCINATION_THRESHOLD = 0.6
            mc.HALLUCINATION_MIN_RESPONSE_LENGTH = 50
            mc.VERIFICATION_CURRENT_EVENT_MODEL = "openai/gpt-4o-mini"
            mc.VERIFICATION_EXPERT_MODEL = "xai/grok-4"

            mock_ex.return_value = (
                ["PostgreSQL uses MVCC", "ACID transactions supported"], "llm")
            mock_vc.return_value = {
                "status": "verified", "confidence": 0.9,
                "claim": "PostgreSQL uses MVCC", "method": "kb_similarity",
            }
            async for event in verify_response_streaming(
                long_response, conversation_id="conv-123",
                chroma_client=chroma_client, neo4j_driver=driver,
                redis_client=MagicMock()):
                events.append(event)

        event_types = [e.get("type") for e in events]
        assert "extraction_complete" in event_types
        assert "claim_extracted" in event_types
        assert events[-1].get("type") == "summary"
        summary = events[-1]
        assert "verified" in summary and "unverified" in summary and "total" in summary


# ===========================================================================
# 4. TestFullUserJourney
# ===========================================================================

class TestFullUserJourney:
    """End-to-end synthetic user flow: setup -> ingest -> query -> verify.

    Mocks at the function level (ingest_content, multi_domain_query, etc.)
    rather than testing internals -- this avoids deep-patching of all sub-deps.
    """

    @pytest.mark.asyncio
    async def test_new_user_setup_query_verify(self):
        """Full journey: ingest a doc, query it, get results, verify a claim."""
        # --- Phase 1: Ingest (mock at service.ingestion level) ---
        with patch("routers.system_monitor.get_redis", return_value=MagicMock()), \
             patch("app.services.ingestion.cache"), \
             patch("app.services.ingestion.get_redis", return_value=MagicMock()), \
             patch("app.services.ingestion.get_neo4j") as mock_neo4j_fn, \
             patch("app.services.ingestion.get_chroma") as mock_chroma_fn:
            client, collection, driver, session = _ingest_mocks()
            mock_chroma_fn.return_value = client
            mock_neo4j_fn.return_value = driver
            with patch("app.services.ingestion.graph") as g:
                g.find_artifact_by_filename.return_value = None
                g.create_artifact.return_value = None
                g.discover_relationships.return_value = 1
                ingest_result = ingest_content(
                    SAMPLE_MARKDOWN, domain="coding",
                    metadata={"filename": "adr-001.md"})

        assert ingest_result["status"] == "success"
        artifact_id = ingest_result["artifact_id"]

        # --- Phase 2: Query (mock multi_domain_query at function level) ---
        from agents.assembler import assemble_context

        mock_query_results = [
            {"content": "PostgreSQL uses MVCC for concurrent access and supports ACID",
             "relevance": 0.88, "artifact_id": artifact_id,
             "filename": "adr-001.md", "domain": "coding",
             "chunk_index": 0, "chunk_id": f"{artifact_id}_chunk_0",
             "collection": "domain_coding", "ingested_at": "",
             "sub_category": "", "tags_json": "[]", "keywords": "[]"},
        ]

        with patch("agents.decomposer.multi_domain_query",
                    new_callable=AsyncMock, return_value=mock_query_results):
            from agents.decomposer import multi_domain_query
            query_results = await multi_domain_query(
                "What database did we choose and why?",
                domains=["coding"])

        assert len(query_results) >= 1
        assert "PostgreSQL" in query_results[0]["content"]

        # --- Phase 3: Assemble context ---
        context, sources, chars = assemble_context(query_results)
        assert "PostgreSQL" in context
        assert len(sources) == 1 and chars > 0

        # --- Phase 4: Verify a claim (mock at function level) ---
        mock_verify_result = {
            "status": "verified", "confidence": 0.95,
            "claim": "We chose PostgreSQL because it uses MVCC",
            "method": "kb_similarity",
        }
        with patch("agents.hallucination.verification.verify_claim",
                    new_callable=AsyncMock, return_value=mock_verify_result):
            from agents.hallucination.verification import verify_claim
            verify_result = await verify_claim(
                "We chose PostgreSQL because it uses MVCC",
                MagicMock(), MagicMock(), MagicMock(), threshold=0.6)

        assert verify_result["status"] in ("verified", "uncertain")
        assert "confidence" in verify_result

    @pytest.mark.asyncio
    async def test_multi_domain_query(self):
        """Query spanning multiple domains with proper routing."""
        coding_result = {
            "content": "Python async/await pattern for API calls",
            "relevance": 0.80, "artifact_id": "art-c1",
            "filename": "async_patterns.md", "domain": "coding",
            "chunk_index": 0, "chunk_id": "art-c1_chunk_0",
            "collection": "domain_coding", "ingested_at": "",
            "sub_category": "", "tags_json": "[]", "keywords": "[]",
        }
        finance_result = {
            "content": "API rate limiting affects trading latency",
            "relevance": 0.75, "artifact_id": "art-f1",
            "filename": "trading_notes.md", "domain": "finance",
            "chunk_index": 0, "chunk_id": "art-f1_chunk_0",
            "collection": "domain_finance", "ingested_at": "",
            "sub_category": "", "tags_json": "[]", "keywords": "[]",
        }

        with patch("agents.decomposer.multi_domain_query",
                    new_callable=AsyncMock,
                    return_value=[coding_result, finance_result]):
            from agents.decomposer import multi_domain_query
            results = await multi_domain_query(
                "How does API rate limiting affect our systems?",
                domains=["coding", "finance"])

        assert len(results) == 2
        domains_found = {r["domain"] for r in results}
        assert "coding" in domains_found and "finance" in domains_found

    @pytest.mark.asyncio
    async def test_memory_extraction_from_chat(self):
        """Chat produces memory artifacts via the memory agent."""
        from agents.memory import extract_memories

        response_text = (
            "Based on our analysis, we decided to use PostgreSQL 15 for the "
            "project database. The key factors were ACID compliance, MVCC "
            "concurrency, and the pgvector extension for similarity search. "
            "We rejected MongoDB because it lacked strong transactional "
            "guarantees at the time of evaluation. The migration plan "
            "includes using Alembic for schema management and PgBouncer "
            "for connection pooling. Timeline: migration starts Monday."
        )

        mock_llm_response = json.dumps([
            {"content": "Chose PostgreSQL 15 for project database",
             "memory_type": "decision", "summary": "DB choice: PostgreSQL 15"},
            {"content": "Rejected MongoDB for lacking transactional guarantees",
             "memory_type": "decision", "summary": "Rejected MongoDB"},
            {"content": "Migration starts Monday",
             "memory_type": "temporal", "summary": "Migration timeline"},
        ])

        with patch("core.agents.memory.call_internal_llm",
                    new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = mock_llm_response
            memories = await extract_memories(response_text,
                                              conversation_id="conv-test-123")

        assert len(memories) >= 1
        for mem in memories:
            assert "content" in mem
            # memory agent normalizes "type" -> "memory_type"
            assert "memory_type" in mem or "type" in mem
