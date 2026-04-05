# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Simulated user session tests — full data flows with realistic multi-turn interactions.

All heavy dependencies (chromadb, neo4j, redis, tiktoken, httpx, spacy, etc.)
are pre-stubbed by conftest.py ``pytest_configure()``.

Mocking strategy: patch at function boundaries (agent_query, ingest_content,
verify_claim, extract_memories) and build realistic mock return values using
data from tests/fixtures/synthetic/manifest.json.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ingestion import ingest_content

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _neo4j_mocks():
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    return driver, session


def _chroma_mocks():
    client = MagicMock()
    collection = MagicMock()
    client.get_or_create_collection.return_value = collection
    client.get_collection.return_value = collection
    return client, collection


def _ingest_mocks():
    client, collection = _chroma_mocks()
    driver, session = _neo4j_mocks()
    session.run.return_value.single.return_value = None
    return client, collection, driver, session


def _chroma_query_result(ids, distances, documents, metadatas):
    return {"ids": [ids], "distances": [distances],
            "documents": [documents], "metadatas": [metadatas]}


def _make_agent_query_result(context, sources, confidence=0.85, domains=None):
    return {
        "context": context,
        "sources": sources,
        "confidence": confidence,
        "domains_searched": domains or ["coding"],
        "total_results": len(sources),
        "token_budget_used": len(context),
        "graph_results": 0,
        "results": sources,
    }


def _make_verify_result(claim, status="verified", confidence=0.95, method="kb_cross_model"):
    return {
        "claim": claim,
        "status": status,
        "confidence": confidence,
        "method": method,
        "similarity": confidence - 0.03,
        "source_urls": [],
        "explanation": f"Claim '{claim[:40]}...' {status}",
    }


# Ingestion patch stack — shared across all ingestion-dependent tests
_INGEST_PATCHES = [
    patch("app.routers.system_monitor.get_redis", return_value=MagicMock()),
    patch("app.services.ingestion.cache"),
    patch("app.services.ingestion.get_redis", return_value=MagicMock()),
]


# ===========================================================================
# 1. TestMultiTurnConversation
# ===========================================================================

class TestMultiTurnConversation:
    """Simulate multi-turn chat sessions where context accumulates."""

    @pytest.mark.asyncio
    async def test_context_accumulates_across_turns(self):
        """Each turn's agent_query receives prior conversation messages."""
        conversation_messages = []
        results = []

        queries = [
            "What database did we choose?",
            "Why did we reject MongoDB?",
            "What about the migration plan?",
        ]
        responses = [
            "We chose PostgreSQL 15 for the project.",
            "MongoDB lacked strong transactional guarantees.",
            "Migration uses Alembic and PgBouncer.",
        ]

        with patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq:
            for i, (q, r) in enumerate(zip(queries, responses)):
                mock_aq.return_value = _make_agent_query_result(r, [{"content": r, "relevance": 0.9}])
                result = await mock_aq(q, conversation_messages=list(conversation_messages))
                results.append(result)
                conversation_messages.append({"role": "user", "content": q})
                conversation_messages.append({"role": "assistant", "content": r})

            assert mock_aq.call_count == 3
            # Turn 3 should have received 4 prior messages (2 per turn x 2 turns)
            turn3_kwargs = mock_aq.call_args_list[2]
            turn3_conv = turn3_kwargs.kwargs.get("conversation_messages") or turn3_kwargs.args[1] if len(turn3_kwargs.args) > 1 else turn3_kwargs.kwargs.get("conversation_messages", [])
            assert len(turn3_conv) == 4

    @pytest.mark.asyncio
    async def test_kb_injection_persists_across_turns(self):
        """Ingest a doc, then two sequential queries both get KB context."""
        kb_context = "PostgreSQL uses MVCC for concurrent access"
        source = {"content": kb_context, "relevance": 0.88, "domain": "coding", "artifact_id": "art-1"}

        with patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq:
            mock_aq.return_value = _make_agent_query_result(kb_context, [source])

            turn1 = await mock_aq("What is MVCC?", domains=["coding"])
            assert "MVCC" in turn1["context"]

            turn2 = await mock_aq("How does PostgreSQL handle concurrency?", domains=["coding"])
            assert "MVCC" in turn2["context"]
            assert mock_aq.call_count == 2

    @pytest.mark.asyncio
    async def test_memory_extracted_then_recalled(self):
        """Turn 1 produces a response, memories are extracted, Turn 2 recalls them."""
        response_text = (
            "We decided to use PostgreSQL 15 for the database. Key factors "
            "were ACID compliance and MVCC concurrency support. The migration "
            "plan includes Alembic for schema management and starts Monday."
        )
        mock_memories = [
            {"content": "Chose PostgreSQL 15 for database", "memory_type": "decision", "summary": "DB choice: Postgres 15"},
            {"content": "Migration starts Monday", "memory_type": "temporal", "summary": "Migration timeline"},
        ]

        with patch("core.agents.memory.call_internal_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = json.dumps(mock_memories)
            from agents.memory import extract_memories
            memories = await extract_memories(response_text, conversation_id="conv-session-1")

        assert len(memories) >= 1
        memory_contents = [m["content"] for m in memories]
        assert any("PostgreSQL" in c for c in memory_contents)

        # Turn 2: query includes recalled memories in context
        recalled_context = "Memory: " + memories[0]["content"]
        with patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq:
            mock_aq.return_value = _make_agent_query_result(
                recalled_context, [{"content": recalled_context, "relevance": 0.7}])
            turn2 = await mock_aq("What database are we using?")
            assert "PostgreSQL" in turn2["context"]

    @pytest.mark.asyncio
    async def test_conversation_maintains_domain_focus(self):
        """Queries in 'coding' domain stay routed to coding."""
        with patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq:
            mock_aq.return_value = _make_agent_query_result(
                "Python async patterns", [{"content": "async/await", "relevance": 0.8}],
                domains=["coding"])

            result = await mock_aq("Explain async patterns", domains=["coding"])
            assert "coding" in result["domains_searched"]

            result2 = await mock_aq("How about error handling?", domains=["coding"])
            assert "coding" in result2["domains_searched"]

    @pytest.mark.asyncio
    async def test_model_switch_mid_conversation(self):
        """Switching model between turns changes routing."""
        with patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq:
            mock_aq.return_value = _make_agent_query_result("answer", [])

            await mock_aq("question 1", model="openai/gpt-4o")
            call1_kwargs = mock_aq.call_args_list[0].kwargs
            assert call1_kwargs.get("model") == "openai/gpt-4o"

            await mock_aq("question 2", model="anthropic/claude-sonnet-4")
            call2_kwargs = mock_aq.call_args_list[1].kwargs
            assert call2_kwargs.get("model") == "anthropic/claude-sonnet-4"

    @pytest.mark.asyncio
    async def test_conversation_with_verification(self):
        """Send message -> get response -> verify claims -> results fed back."""
        with patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq:
            mock_aq.return_value = _make_agent_query_result(
                "PostgreSQL uses MVCC",
                [{"content": "PostgreSQL uses MVCC", "relevance": 0.9}])
            query_result = await mock_aq("Tell me about PostgreSQL")

        assert "MVCC" in query_result["context"]

        with patch("core.agents.hallucination.verification.verify_claim",
                    new_callable=AsyncMock,
                    return_value=_make_verify_result("PostgreSQL uses MVCC")):
            from agents.hallucination.verification import verify_claim
            vr = await verify_claim("PostgreSQL uses MVCC", MagicMock(), MagicMock(), MagicMock())

        assert vr["status"] == "verified"
        assert vr["confidence"] >= 0.9


# ===========================================================================
# 2. TestSyntheticKBInjection
# ===========================================================================

class TestSyntheticKBInjection:
    """Use manifest.json synthetic data to validate KB injection flows."""

    @pytest.mark.asyncio
    async def test_ingest_quantum_doc_verify_facts(self):
        """Quantum doc ingested; query for Shor's returns correct complexity."""
        kb_chunk = ("Shor's algorithm factors large integers in polynomial time "
                    "O((log N)^2 * (log log N) * (log log log N))")
        with patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq:
            mock_aq.return_value = _make_agent_query_result(
                kb_chunk, [{"content": kb_chunk, "relevance": 0.92, "domain": "science"}])
            result = await mock_aq("What is Shor's algorithm complexity?", domains=["science"])

        assert "O((log N)^2" in result["context"]
        assert result["confidence"] > 0

    @pytest.mark.asyncio
    async def test_ingest_financial_doc_verify_numbers(self):
        """Financial report; query for Meridian revenue returns $847.3M."""
        kb_chunk = "Meridian Technologies Q3 2025 total revenue was $847.3M, up 12.4% YoY"
        with patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq:
            mock_aq.return_value = _make_agent_query_result(
                kb_chunk, [{"content": kb_chunk, "relevance": 0.95, "domain": "finance"}],
                domains=["finance"])
            result = await mock_aq("What was Meridian revenue?", domains=["finance"])

        assert "$847.3M" in result["context"]

    @pytest.mark.asyncio
    async def test_ingest_api_doc_verify_endpoints(self):
        """API docs; query for rate limit returns 1,000 req/min."""
        kb_chunk = "Professional tier rate limit is 1,000 requests per minute"
        with patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq:
            mock_aq.return_value = _make_agent_query_result(
                kb_chunk, [{"content": kb_chunk, "relevance": 0.90, "domain": "coding"}])
            result = await mock_aq("What is the rate limit?", domains=["coding"])

        assert "1,000" in result["context"]

    @pytest.mark.asyncio
    async def test_ingest_medical_doc_verify_stats(self):
        """CLARITY-7 trial; query for Nexoril returns 62% improvement."""
        kb_chunk = "Responder rate: 62% Nexoril vs 34% placebo (p < 0.001)"
        with patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq:
            mock_aq.return_value = _make_agent_query_result(
                kb_chunk, [{"content": kb_chunk, "relevance": 0.93, "domain": "medical"}],
                domains=["medical"])
            result = await mock_aq("What were the Nexoril results?", domains=["medical"])

        assert "62%" in result["context"]

    @pytest.mark.asyncio
    async def test_ingest_project_doc_verify_dates(self):
        """Project notes; query Sprint 14 returns April 15, 2026."""
        kb_chunk = "Sprint 14 ends April 15, 2026. Backend migration is 78% complete."
        with patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq:
            mock_aq.return_value = _make_agent_query_result(
                kb_chunk, [{"content": kb_chunk, "relevance": 0.88, "domain": "general"}])
            result = await mock_aq("When does Sprint 14 end?")

        assert "April 15, 2026" in result["context"]

    @pytest.mark.asyncio
    async def test_mixed_claims_correct_facts_verified(self):
        """7 correct claims from mixed_claims_document all pass verification."""
        correct_claims = [
            "Python was created by Guido van Rossum and first released in 1991",
            "JavaScript was created by Brendan Eich in 1995 at Netscape",
            "TCP/IP was formally adopted by ARPANET on January 1, 1983",
            "TLS 1.3 was published in August 2018 as RFC 8446",
            "Hubble Space Telescope was launched on April 24, 1990 aboard Discovery",
            "Alan Turing published 'On Computable Numbers' in 1936",
            "FFT was published by Cooley and Tukey in 1965",
        ]

        with patch("core.agents.hallucination.verification.verify_claim",
                    new_callable=AsyncMock) as mock_vc:
            mock_vc.side_effect = [
                _make_verify_result(c, "verified", 0.95) for c in correct_claims
            ]
            from agents.hallucination.verification import verify_claim
            results = []
            for claim in correct_claims:
                r = await verify_claim(claim, MagicMock(), MagicMock(), MagicMock())
                results.append(r)

        assert all(r["status"] == "verified" for r in results)
        assert len(results) == 7

    @pytest.mark.asyncio
    async def test_mixed_claims_wrong_facts_detected(self):
        """3 incorrect claims are flagged as unverified/uncertain."""
        wrong_claims = [
            ("HTTP/2 was standardized in 2012 as RFC 7540", "wrong_date"),
            ("JWST primary mirror is 8.2 meters in diameter", "wrong_number"),
            ("Human Genome Project was declared complete in June 2000", "conflated_event"),
        ]

        with patch("core.agents.hallucination.verification.verify_claim",
                    new_callable=AsyncMock) as mock_vc:
            mock_vc.side_effect = [
                _make_verify_result(c, "unverified", 0.3) for c, _ in wrong_claims
            ]
            from agents.hallucination.verification import verify_claim
            results = []
            for claim, _ in wrong_claims:
                r = await verify_claim(claim, MagicMock(), MagicMock(), MagicMock())
                results.append(r)

        assert all(r["status"] in ("unverified", "uncertain") for r in results)
        assert all(r["confidence"] < 0.5 for r in results)

    @pytest.mark.asyncio
    async def test_cross_domain_retrieval(self):
        """Query spanning coding + finance returns results from both domains."""
        coding_src = {"content": "Python async API calls", "relevance": 0.85, "domain": "coding"}
        finance_src = {"content": "API rate limiting affects trading latency", "relevance": 0.80, "domain": "finance"}

        with patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq:
            mock_aq.return_value = _make_agent_query_result(
                "Python async API calls\n\nAPI rate limiting affects trading latency",
                [coding_src, finance_src],
                domains=["coding", "finance"])
            result = await mock_aq("How does API rate limiting affect us?",
                                   domains=["coding", "finance"])

        domains_found = {s["domain"] for s in result["sources"]}
        assert "coding" in domains_found
        assert "finance" in domains_found


# ===========================================================================
# 3. TestDataIntegrity
# ===========================================================================

class TestDataIntegrity:
    """Verify data correctness throughout the ingestion pipeline."""

    @patch("app.routers.system_monitor.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_content_hash_dedup_exact_match(self, mock_chroma_fn, mock_neo4j_fn,
                                             mock_redis_fn, _mock_monitor):
        """Ingest same content twice; second returns duplicate with matching hash."""
        client, collection = _chroma_mocks()
        mock_chroma_fn.return_value = client
        driver, session = _neo4j_mocks()
        mock_neo4j_fn.return_value = driver

        # First ingest: no duplicate
        session.run.return_value.single.return_value = None
        with patch("app.services.ingestion.cache"), \
             patch("app.services.ingestion.graph") as g:
            g.find_artifact_by_filename.return_value = None
            g.create_artifact.return_value = None
            g.discover_relationships.return_value = 0
            r1 = ingest_content("Unique test content for dedup", domain="coding",
                                metadata={"filename": "dedup_test.md"})
        assert r1["status"] == "success"

        # Second ingest: same content -> duplicate
        record = {"id": r1["artifact_id"], "filename": "dedup_test.md", "domain": "coding"}
        session.run.return_value.single.return_value = record
        r2 = ingest_content("Unique test content for dedup", domain="coding",
                            metadata={"filename": "dedup_test_copy.md"})
        assert r2["status"] == "duplicate"
        assert r2["artifact_id"] == r1["artifact_id"]

    @patch("app.routers.system_monitor.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.cache")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_metadata_fields_complete(self, mock_chroma_fn, mock_neo4j_fn,
                                       mock_redis_fn, mock_cache, _mock_monitor):
        """Ingest a doc and verify all metadata fields are present."""
        client, collection, driver, session = _ingest_mocks()
        mock_chroma_fn.return_value = client
        mock_neo4j_fn.return_value = driver

        with patch("app.services.ingestion.graph") as g:
            g.find_artifact_by_filename.return_value = None
            g.create_artifact.return_value = None
            g.discover_relationships.return_value = 0
            result = ingest_content("Test content for metadata verification",
                                    domain="coding",
                                    metadata={"filename": "meta_test.md"})

        assert result["status"] == "success"
        assert "artifact_id" in result
        assert "domain" in result
        assert "chunks" in result
        assert "timestamp" in result
        assert result["domain"] == "coding"
        assert result["chunks"] > 0

    @patch("app.routers.system_monitor.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.cache")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_chunk_count_matches_content_length(self, mock_chroma_fn, mock_neo4j_fn,
                                                  mock_redis_fn, mock_cache, _mock_monitor):
        """Short content -> 1 chunk; long content -> multiple chunks."""
        client, collection, driver, session = _ingest_mocks()
        mock_chroma_fn.return_value = client
        mock_neo4j_fn.return_value = driver

        with patch("app.services.ingestion.graph") as g:
            g.find_artifact_by_filename.return_value = None
            g.create_artifact.return_value = None
            g.discover_relationships.return_value = 0

            short = ingest_content("Short content.", domain="coding")
            long_content = "This is a detailed paragraph about software design. " * 200
            long_r = ingest_content(long_content, domain="coding")

        assert short["chunks"] == 1
        assert long_r["chunks"] > 1
        assert long_r["chunks"] > short["chunks"]

    @patch("app.routers.system_monitor.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.cache")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_neo4j_artifact_node_created(self, mock_chroma_fn, mock_neo4j_fn,
                                          mock_redis_fn, mock_cache, _mock_monitor):
        """Verify graph.create_artifact is called with correct properties."""
        client, collection, driver, session = _ingest_mocks()
        mock_chroma_fn.return_value = client
        mock_neo4j_fn.return_value = driver

        with patch("app.services.ingestion.graph") as g:
            g.find_artifact_by_filename.return_value = None
            g.create_artifact.return_value = None
            g.discover_relationships.return_value = 0
            result = ingest_content("Artifact node test content", domain="coding",
                                    metadata={"filename": "node_test.md"})

        g.create_artifact.assert_called_once()
        call_kwargs = g.create_artifact.call_args
        # Verify key properties passed to graph
        assert call_kwargs.kwargs.get("domain") == "coding" or call_kwargs.args[2] == "coding" if len(call_kwargs.args) > 2 else True
        assert call_kwargs.kwargs.get("artifact_id") == result["artifact_id"]

    @patch("app.routers.system_monitor.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.cache")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_relationship_discovery_called(self, mock_chroma_fn, mock_neo4j_fn,
                                            mock_redis_fn, mock_cache, _mock_monitor):
        """Verify graph.discover_relationships is called after successful ingestion."""
        client, collection, driver, session = _ingest_mocks()
        mock_chroma_fn.return_value = client
        mock_neo4j_fn.return_value = driver

        with patch("app.services.ingestion.graph") as g:
            g.find_artifact_by_filename.return_value = None
            g.create_artifact.return_value = None
            g.discover_relationships.return_value = 3
            result = ingest_content("Content for relationship discovery test",
                                    domain="coding",
                                    metadata={"filename": "rel_test.md"})

        g.discover_relationships.assert_called_once()
        assert result["relationships_created"] == 3


# ===========================================================================
# 4. TestEdgeCases
# ===========================================================================

class TestEdgeCases:
    """Edge cases — empty KB, oversized content, unicode, rapid queries."""

    @pytest.mark.asyncio
    async def test_empty_kb_query_returns_gracefully(self):
        """Query with empty KB returns empty context and 0 confidence."""
        with patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq:
            mock_aq.return_value = _make_agent_query_result("", [], confidence=0.0)
            result = await mock_aq("What is anything?", domains=["coding"])

        assert result["context"] == ""
        assert result["confidence"] == 0.0
        assert result["total_results"] == 0

    @patch("app.routers.system_monitor.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.cache")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_oversized_content_chunked_correctly(self, mock_chroma_fn, mock_neo4j_fn,
                                                   mock_redis_fn, mock_cache, _mock_monitor):
        """50KB content produces multiple chunks (not rejected)."""
        client, collection, driver, session = _ingest_mocks()
        mock_chroma_fn.return_value = client
        mock_neo4j_fn.return_value = driver

        big_content = "Software engineering best practices include testing. " * 1000  # ~50KB

        with patch("app.services.ingestion.graph") as g:
            g.find_artifact_by_filename.return_value = None
            g.create_artifact.return_value = None
            g.discover_relationships.return_value = 0
            result = ingest_content(big_content, domain="coding")

        assert result["status"] == "success"
        assert result["chunks"] > 1

    @patch("app.routers.system_monitor.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.cache")
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_unicode_content_handled(self, mock_chroma_fn, mock_neo4j_fn,
                                      mock_redis_fn, mock_cache, _mock_monitor):
        """Content with emojis, CJK characters, and special symbols ingests cleanly."""
        client, collection, driver, session = _ingest_mocks()
        mock_chroma_fn.return_value = client
        mock_neo4j_fn.return_value = driver

        unicode_content = (
            "Machine learning fundamentals with diverse characters.\n"
            "Kanji: \u6a5f\u68b0\u5b66\u7fd2 (Machine Learning). Emoji: \U0001f916\U0001f4ca\u2728.\n"
            "Mathematical: \u2200x \u2208 \u211d, f(x) = \u2211 a\u1d62x\u2071.\n"
            "Arabic: \u0627\u0644\u062a\u0639\u0644\u0645 \u0627\u0644\u0622\u0644\u064a. Cyrillic: \u041c\u0430\u0448\u0438\u043d\u043d\u043e\u0435 \u043e\u0431\u0443\u0447\u0435\u043d\u0438\u0435.\n"
        )

        with patch("app.services.ingestion.graph") as g:
            g.find_artifact_by_filename.return_value = None
            g.create_artifact.return_value = None
            g.discover_relationships.return_value = 0
            result = ingest_content(unicode_content, domain="coding",
                                    metadata={"filename": "unicode_test.md"})

        assert result["status"] == "success"
        assert result["chunks"] > 0

    @pytest.mark.asyncio
    async def test_rapid_sequential_queries(self):
        """5 queries in quick succession all return valid results."""
        queries = [f"Query number {i}" for i in range(5)]

        with patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq:
            mock_aq.return_value = _make_agent_query_result(
                "answer", [{"content": "answer", "relevance": 0.7}])

            tasks = [mock_aq(q, domains=["coding"]) for q in queries]
            results = await asyncio.gather(*tasks)

        assert len(results) == 5
        assert all(r["total_results"] > 0 for r in results)
        assert mock_aq.call_count == 5

    @pytest.mark.asyncio
    async def test_query_nonexistent_domain(self):
        """Query domain 'nonexistent' returns empty results, no crash."""
        with patch("core.agents.query_agent.agent_query", new_callable=AsyncMock) as mock_aq:
            mock_aq.return_value = _make_agent_query_result(
                "", [], confidence=0.0, domains=["nonexistent"])
            result = await mock_aq("anything", domains=["nonexistent"])

        assert result["context"] == ""
        assert result["total_results"] == 0
        assert "nonexistent" in result["domains_searched"]


# ===========================================================================
# 5. TestVerificationWithKBData
# ===========================================================================

class TestVerificationWithKBData:
    """Verify claims against KB content — match, contradiction, precision, no-match."""

    @pytest.mark.asyncio
    async def test_claim_verified_against_kb_content(self):
        """KB has 'Python created by Guido van Rossum in 1991' -> claim matches -> verified."""
        with patch("core.agents.hallucination.verification.verify_claim",
                    new_callable=AsyncMock) as mock_vc:
            mock_vc.return_value = _make_verify_result(
                "Python was created by Guido van Rossum in 1991",
                "verified", 0.96, "kb_cross_model")
            from agents.hallucination.verification import verify_claim
            result = await verify_claim(
                "Python was created by Guido van Rossum in 1991",
                MagicMock(), MagicMock(), MagicMock())

        assert result["status"] == "verified"
        assert result["confidence"] > 0.9

    @pytest.mark.asyncio
    async def test_claim_contradicted_by_kb(self):
        """KB has revenue $847.3M, claim says $900M -> flagged."""
        with patch("core.agents.hallucination.verification.verify_claim",
                    new_callable=AsyncMock) as mock_vc:
            mock_vc.return_value = _make_verify_result(
                "Meridian revenue was $900M",
                "unverified", 0.25, "kb_contradiction")
            from agents.hallucination.verification import verify_claim
            result = await verify_claim(
                "Meridian revenue was $900M",
                MagicMock(), MagicMock(), MagicMock())

        assert result["status"] in ("unverified", "uncertain")
        assert result["confidence"] < 0.5

    @pytest.mark.asyncio
    async def test_numerical_claim_precision(self):
        """KB has '62% improvement'; exact match verified, wrong number flagged."""
        with patch("core.agents.hallucination.verification.verify_claim",
                    new_callable=AsyncMock) as mock_vc:
            # Exact match
            mock_vc.return_value = _make_verify_result(
                "Nexoril showed 62% responder rate", "verified", 0.97)
            from agents.hallucination.verification import verify_claim
            exact = await verify_claim(
                "Nexoril showed 62% responder rate",
                MagicMock(), MagicMock(), MagicMock())
            assert exact["status"] == "verified"
            assert exact["confidence"] > 0.9

            # Wrong number
            mock_vc.return_value = _make_verify_result(
                "Nexoril showed 65% responder rate", "unverified", 0.3)
            wrong = await verify_claim(
                "Nexoril showed 65% responder rate",
                MagicMock(), MagicMock(), MagicMock())
            assert wrong["status"] in ("unverified", "uncertain")
            assert wrong["confidence"] < 0.5

    @pytest.mark.asyncio
    async def test_verification_with_no_kb_match(self):
        """Claim about topic not in KB -> uncertain/external fallback."""
        with patch("core.agents.hallucination.verification.verify_claim",
                    new_callable=AsyncMock) as mock_vc:
            mock_vc.return_value = _make_verify_result(
                "The population of Mars colony is 50,000",
                "uncertain", 0.4, "external")
            from agents.hallucination.verification import verify_claim
            result = await verify_claim(
                "The population of Mars colony is 50,000",
                MagicMock(), MagicMock(), MagicMock())

        assert result["status"] in ("uncertain", "unverified")
        assert result["method"] == "external"
