# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""End-to-end integration tests: ingest -> query -> retrieve -> verify.

All heavy dependencies (chromadb, neo4j, redis, tiktoken, httpx, spacy,
pdfplumber, etc.) are pre-stubbed by conftest.py's ``pytest_configure()``.

Mocking strategy mirrors test_services_ingestion.py:
- Patch ``services.ingestion.get_redis/get_neo4j/get_chroma`` for ingest tests
- Patch ``core.agents.query_agent.config`` (set ``.DOMAINS`` on the mock) for query tests
- Patch verification internals at their own module paths
- TestFullUserJourney mocks at function level (ingest_content, multi_domain_query, etc.)
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ingestion import ingest_content

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

    @patch("app.routers.system_monitor.get_redis", return_value=MagicMock())
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
        collection.upsert.assert_called_once()
        g.create_artifact.assert_called_once()

    @patch("app.routers.system_monitor.get_redis", return_value=MagicMock())
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
        collection.upsert.assert_not_called()

    @patch("app.routers.system_monitor.get_redis", return_value=MagicMock())
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

    @patch("app.routers.system_monitor.get_redis", return_value=MagicMock())
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
        add_call = collection.upsert.call_args
        ids = add_call.kwargs.get("ids") or add_call.args[0]
        assert len(ids) == result["chunks"]

    @patch("app.routers.system_monitor.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_redis", return_value=MagicMock())
    @patch("app.services.ingestion.get_neo4j")
    @patch("app.services.ingestion.get_chroma")
    def test_ingest_rollback_on_chroma_failure(self, mock_chroma_fn, mock_neo4j_fn, mock_redis_fn, _mock_monitor_redis):
        client, collection = _chroma_mocks()
        collection.upsert.side_effect = RuntimeError("ChromaDB connection timeout")
        mock_chroma_fn.return_value = client
        driver, session = _neo4j_mocks()
        mock_neo4j_fn.return_value = driver
        session.run.return_value.single.return_value = None

        # ChromaDB write failures propagate as uncaught exceptions
        with pytest.raises(RuntimeError, match="ChromaDB connection timeout"):
            ingest_content("some content to ingest", domain="coding")

    @patch("app.routers.system_monitor.get_redis", return_value=MagicMock())
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

    Patches target core.agents.query_agent (where multi_domain_query lives) and
    core.retrieval.bm25 (which multi_domain_query calls internally).
    """

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    @staticmethod
    def _configure(monkeypatch, domains):
        """Pin the retrieval-relevant settings on the REAL config module.

        The real ``config.collection_name`` already maps d -> "domain_<d>",
        so the tests' Chroma stubs keep working without a canned lambda.
        """
        for name, value in {
            "DOMAINS": domains,
            "HYBRID_VECTOR_WEIGHT": 0.6,
            "HYBRID_KEYWORD_WEIGHT": 0.4,
            "CROSS_DOMAIN_DEFAULT_AFFINITY": 0.0,
            "DOMAIN_AFFINITY": {},
        }.items():
            monkeypatch.setattr(f"core.agents.query_agent.config.{name}", value)

    def test_query_returns_relevant_chunks(self, monkeypatch):
        self._configure(monkeypatch, ["coding", "general"])

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

        from core.agents.query_agent import multi_domain_query
        with patch("core.retrieval.bm25.is_available", return_value=False):
            results = self._run(multi_domain_query(
                "What database did we choose?", domains=["coding"],
                chroma_client=chroma_client))

        assert len(results) == 2
        assert results[0]["domain"] == "coding"
        assert results[0]["content"] == "PostgreSQL uses MVCC"
        assert results[0]["relevance"] > results[1]["relevance"]

    def test_query_hybrid_search(self, monkeypatch):
        self._configure(monkeypatch, ["coding"])

        collection = MagicMock()
        collection.query.return_value = _chroma_query_result(
            ["chunk_1"], [0.3],
            ["PostgreSQL uses MVCC for concurrency"],
            [{"artifact_id": "art-1", "filename": "adr.md", "chunk_index": 0}],
        )
        chroma_client = MagicMock()
        chroma_client.get_collection.return_value = collection
        chroma_client.list_collections.return_value = []

        from core.agents.query_agent import multi_domain_query
        with patch("core.retrieval.bm25.is_available", return_value=True), \
             patch("core.retrieval.bm25.search_bm25", return_value=[("chunk_1", 0.9)]):
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
    async def test_query_private_mode_l2_skips_kb_retrieval(self):
        """Level 2 privacy must short-circuit BEFORE any KB retrieval runs.

        This used to patch ``agent_query`` and then call the mock, asserting
        that a locally-written dict had ``total_results == 0`` — which it did,
        because the test wrote it. It could not have caught the gate being
        removed. The old docstring justified that by citing agent_query's deep
        dependencies, but the L2 contract does not live in agent_query at all:
        it is an early return in the ``/query`` router
        (``app/routers/query.py:83``), and it is reachable with nothing mocked
        except the private-mode level itself.

        The server-side enforcement matters because no response field signals
        the bypass — QueryEndpointResponse has no ``extra="allow"``, so the
        empty result IS the signal. If the gate regressed, a Level-2 user's
        query would silently reach the KB.
        """
        from app.routers.query import query_endpoint

        called: list[str] = []

        async def _tripwire(*_a, **_kw):
            called.append("agent_query_full")
            raise AssertionError("KB retrieval ran under Private Mode L2")

        with patch("app.routers.query.private_blocks", return_value=True) as gate, \
             patch("core.agents.query_agent.agent_query_full", new=_tripwire):
            request = MagicMock()
            request.headers = {}
            result = await query_endpoint(
                MagicMock(query="test query", domains=["coding"], n_results=10),
                request,
            )

        gate.assert_called_once_with(2), "the gate must test for level 2, not another tier"
        assert called == [], "retrieval must not be reached at all"
        assert result["context"] == "" and result["sources"] == []
        assert result["confidence"] == 0.0

    def test_query_empty_collection(self, monkeypatch):
        self._configure(monkeypatch, ["coding"])

        collection = MagicMock()
        collection.query.return_value = _chroma_query_result([], [], [], [])
        chroma_client = MagicMock()
        chroma_client.get_collection.return_value = collection
        chroma_client.list_collections.return_value = []

        from core.agents.query_agent import multi_domain_query
        with patch("core.retrieval.bm25.is_available", return_value=False):
            results = self._run(multi_domain_query(
                "anything", domains=["coding"], chroma_client=chroma_client))
        assert results == []

    def test_query_domain_filtering(self, monkeypatch):
        self._configure(monkeypatch, ["coding", "finance"])

        coding_coll = MagicMock()
        coding_coll.query.return_value = _chroma_query_result(
            ["c1"], [0.1], ["Python async patterns"],
            [{"artifact_id": "a1", "filename": "async.py", "chunk_index": 0}],
        )
        chroma_client = MagicMock()
        chroma_client.get_collection.return_value = coding_coll
        chroma_client.list_collections.return_value = []

        from core.agents.query_agent import multi_domain_query
        with patch("core.retrieval.bm25.is_available", return_value=False):
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
    core.utils.internal_llm.call_internal_llm (the LLM call site).
    """

    # Until 2026-07-30 this class opened with three tests that patched
    # ``verify_claim``, immediately called the patch, and asserted the fixture
    # they had just supplied — exercising unittest.mock and nothing else, while
    # counting toward the e2e suite. They are replaced by the coverage they
    # claimed: a real run of the streaming pipeline, with only the leaf verifier
    # mocked, asserting that each distinct verdict actually reaches the SSE
    # stream and the summary. Per-claim verifier behaviour is unit-tested in
    # test_hallucination.py; what belongs *here* is the wiring between them.
    @staticmethod
    def _streaming_env(monkeypatch):
        """Override only the gates, on the real config module.

        ``patch("...streaming.config")`` replaces the whole module with a
        MagicMock, so every attribute the test does not explicitly set becomes a
        MagicMock too. The verification loop compares several of them against
        ints, dies on ``'<=' not supported between MagicMock and int``, and
        swallows the error — leaving a stream with claim events but no verdicts.
        Any test asserting only on extraction and summary passes anyway, which
        is how these tests looked healthy while covering nothing.
        """
        import config as real_config
        from core.agents.hallucination import streaming as streaming_mod

        monkeypatch.setattr(streaming_mod.config, "HALLUCINATION_MIN_RESPONSE_LENGTH", 50)
        monkeypatch.setattr(streaming_mod.config, "HALLUCINATION_MAX_CLAIMS", 10)
        monkeypatch.setattr(streaming_mod.config, "HALLUCINATION_THRESHOLD", 0.6)
        monkeypatch.setattr(streaming_mod.config, "ENABLE_VERIFIED_MEMORY_PROMOTION", False)
        return real_config

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status", "confidence", "summary_bucket"),
        [
            ("verified", 0.95, "verified"),
            ("unverified", 0.20, "unverified"),
            ("uncertain", 0.40, "uncertain"),
        ],
    )
    async def test_verdict_propagates_through_the_pipeline(
        self, monkeypatch, status, confidence, summary_bucket,
    ):
        """A verifier verdict must reach both the claim event and the summary.

        The heuristic extractor handles this response, so nothing in the
        extraction path is stubbed — only the leaf verifier is.
        """
        from core.agents.hallucination.streaming import verify_response_streaming

        self._streaming_env(monkeypatch)

        chroma_client, collection = _chroma_mocks()
        collection.query.return_value = _chroma_query_result([], [], [], [])
        driver, _ = _neo4j_mocks()

        long_response = ("PostgreSQL uses MVCC for concurrent access. "
                         "The database supports ACID transactions. "
                         "It was first released in 1996. " * 5)

        events = []
        with patch("core.agents.hallucination.streaming.verify_claim",
                   new_callable=AsyncMock) as mock_vc, \
             patch("core.agents.hallucination.streaming._check_history_consistency",
                   new_callable=AsyncMock, return_value=None), \
             patch("utils.agent_events.emit_agent_event"):
            mock_vc.return_value = {
                "claim": "PostgreSQL uses MVCC",
                "status": status,
                "similarity": confidence,
                "confidence": confidence,
                "verification_method": "kb_cross_model",
            }
            async for event in verify_response_streaming(
                    long_response, conversation_id="conv-verdict",
                    chroma_client=chroma_client, neo4j_driver=driver,
                    redis_client=MagicMock()):
                events.append(event)

        # The verifier was genuinely invoked by the pipeline, not by the test.
        assert mock_vc.await_count >= 1, (
            "pipeline never called verify_claim — the verification loop bailed "
            f"before doing any work. Events: {[e.get('type') for e in events]}"
        )

        verdicts = [e for e in events if e.get("type") == "claim_verified"]
        assert verdicts, (
            f"no claim_verified event emitted; got {[e.get('type') for e in events]}"
        )
        assert all(v["status"] == status for v in verdicts), (
            f"pipeline reshaped the verdict: emitted "
            f"{[v['status'] for v in verdicts]}, verifier returned {status!r}"
        )

        summary = events[-1]
        assert summary.get("type") == "summary"
        assert summary.get(summary_bucket, 0) >= 1, (
            f"verdict {status!r} did not land in the {summary_bucket!r} summary "
            f"bucket: {summary}"
        )

    @pytest.mark.asyncio
    async def test_every_claim_event_carries_a_type(self, monkeypatch):
        """claim_type must be on the wire for every claim.

        The category drives ``promote_verified_facts``' meta-claim filter. While
        it was computed for the SSE event but never written back into the
        persisted claims, "I don't know" answers were eligible for promotion to
        permanent, non-decaying empirical memories.
        """
        from core.agents.hallucination.models import ClaimType
        from core.agents.hallucination.streaming import verify_response_streaming

        self._streaming_env(monkeypatch)

        chroma_client, collection = _chroma_mocks()
        collection.query.return_value = _chroma_query_result([], [], [], [])
        driver, _ = _neo4j_mocks()

        events = []
        with patch("core.agents.hallucination.streaming.verify_claim",
                   new_callable=AsyncMock) as mock_vc, \
             patch("core.agents.hallucination.streaming._check_history_consistency",
                   new_callable=AsyncMock, return_value=None), \
             patch("utils.agent_events.emit_agent_event"):
            mock_vc.return_value = {
                "claim": "PostgreSQL uses MVCC", "status": "verified",
                "similarity": 0.9, "confidence": 0.9,
            }
            async for event in verify_response_streaming(
                    ("PostgreSQL uses MVCC for concurrent access. "
                     "The database supports ACID transactions. "
                     "It was first released in 1996. " * 5),
                    conversation_id="conv-type",
                    chroma_client=chroma_client, neo4j_driver=driver,
                    redis_client=MagicMock()):
                events.append(event)

        typed = [e for e in events if e.get("type") == "claim_extracted"]
        assert typed, "no claim_extracted events emitted"

        known = {c.value for c in ClaimType}
        for event in typed:
            assert event.get("claim_type") in known, (
                f"claim event carries an untypable claim_type "
                f"{event.get('claim_type')!r}; the canonical ClaimType model "
                f"accepts only {sorted(known)}"
            )

    @pytest.mark.asyncio
    async def test_verify_streaming_format(self, monkeypatch):
        """SSE event format: extraction_complete, claim_extracted, claim results, summary.

        Previously patched ``streaming.extract_claims`` — a symbol
        ``verify_response_streaming`` never calls (it uses
        ``_extract_claims_heuristic`` / ``_extract_claims_llm`` directly), so the
        stub was inert and the real extractor ran. Combined with the blanket
        ``config`` mock that killed the verification loop, this asserted only
        that three event types appear. Now it runs the real extraction path and
        additionally pins that verdicts actually arrive.
        """
        from core.agents.hallucination.streaming import verify_response_streaming

        self._streaming_env(monkeypatch)

        chroma_client, collection = _chroma_mocks()
        collection.query.return_value = _chroma_query_result([], [], [], [])
        driver, _ = _neo4j_mocks()

        long_response = ("PostgreSQL uses MVCC for concurrent access. "
                         "The database supports ACID transactions. "
                         "It was first released in 1996. " * 5)

        events = []
        with patch("core.agents.hallucination.streaming.verify_claim", new_callable=AsyncMock) as mock_vc, \
             patch("core.agents.hallucination.streaming._check_history_consistency",
                   new_callable=AsyncMock, return_value=None), \
             patch("utils.agent_events.emit_agent_event"):
            mock_vc.return_value = {
                "status": "verified", "similarity": 0.9, "confidence": 0.9,
                "claim": "PostgreSQL uses MVCC",
                "verification_method": "kb_similarity",
            }
            async for event in verify_response_streaming(
                long_response, conversation_id="conv-123",
                chroma_client=chroma_client, neo4j_driver=driver,
                redis_client=MagicMock()):
                events.append(event)

        event_types = [e.get("type") for e in events]
        assert "extraction_complete" in event_types
        assert "claim_extracted" in event_types
        assert "claim_verified" in event_types, (
            "the stream produced no verdicts — the verification loop never ran. "
            f"Events: {event_types}"
        )
        assert events[-1].get("type") == "summary"
        summary = events[-1]
        assert "verified" in summary and "unverified" in summary and "total" in summary
        assert summary["total"] >= 1


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
        with patch("app.routers.system_monitor.get_redis", return_value=MagicMock()), \
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
        from core.agents.query_agent import assemble_context

        mock_query_results = [
            {"content": "PostgreSQL uses MVCC for concurrent access and supports ACID",
             "relevance": 0.88, "artifact_id": artifact_id,
             "filename": "adr-001.md", "domain": "coding",
             "chunk_index": 0, "chunk_id": f"{artifact_id}_chunk_0",
             "collection": "domain_coding", "ingested_at": "",
             "sub_category": "", "tags_json": "[]", "keywords": "[]"},
        ]

        # --- Phase 2: Assemble context from the retrieved chunks ---
        # There used to be a "query" phase here that patched
        # multi_domain_query and then called it, asserting that
        # mock_query_results contained "PostgreSQL" — which it does, because
        # this test wrote it 10 lines above. It exercised no product code.
        # Real multi-domain retrieval is covered by
        # test_multi_domain_query_merges_across_collections below, which drives
        # the function for real; here the retrieved chunks feed straight into
        # assemble_context, which IS production code.
        query_results = mock_query_results
        context, sources, chars = assemble_context(query_results)
        assert "PostgreSQL" in context
        assert len(sources) == 1 and chars > 0

        # --- Phase 3: the assembled context is what a verifier would receive ---
        # A "verify" phase here previously patched verify_claim and then called
        # it, asserting its own fixture's status was in ("verified",
        # "uncertain") — true of the literal dict it had just written. Claim
        # verification has real coverage in test_nli_verification.py against
        # the actual grounding verifier; repeating a fixture echo here added
        # nothing. What this journey test can honestly assert is that the
        # context handed onward carries the evidence for the claim.
        assert "MVCC" in context, (
            "the assembled context must carry the sentence a downstream "
            "verifier would need to ground the MVCC claim"
        )
        assert sources[0]["artifact_id"] == artifact_id, (
            "context assembly must preserve provenance back to the ingested "
            "artifact — without it a citation cannot be resolved"
        )

    @pytest.mark.asyncio
    async def test_multi_domain_query_merges_across_collections(self):
        """Real multi-domain fan-out: both collections queried, results merged by relevance."""
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

        # Drive the REAL multi_domain_query. It takes chroma_client as a
        # parameter, so the boundary we mock is ChromaDB itself — the thing
        # that genuinely cannot run here — and everything between the call and
        # the merged result is production code. The previous version patched
        # multi_domain_query and then called it, so it asserted only that the
        # two dicts it had written had two different `domain` values.
        from core.agents.query_agent import multi_domain_query

        def _chroma_payload(content: str, chunk_id: str, artifact_id: str,
                            filename: str, distance: float) -> dict:
            return {
                "ids": [[chunk_id]],
                "documents": [[content]],
                "distances": [[distance]],
                "metadatas": [[{"artifact_id": artifact_id, "filename": filename,
                                "chunk_index": 0}]],
            }

        per_collection = {
            "domain_coding": _chroma_payload(
                coding_result["content"], "art-c1_chunk_0", "art-c1",
                "async_patterns.md", 0.30),
            "domain_finance": _chroma_payload(
                finance_result["content"], "art-f1_chunk_0", "art-f1",
                "trading_notes.md", 0.45),
        }

        client = MagicMock()
        client.list_collections.return_value = [
            SimpleNamespace(name=n) for n in per_collection
        ]

        def _get_collection(name: str):
            col = MagicMock()
            col.query.return_value = per_collection[name]
            return col

        client.get_collection.side_effect = lambda name, **_: _get_collection(name)

        # Hybrid BM25 fusion is held off for this test. `search_bm25` reads
        # whatever indexes sit under the RELATIVE `BM25_DATA_DIR`, so what it
        # returns depends on the developer's on-disk corpus and on the cwd
        # pytest was launched from — running from `src/mcp` and from the repo
        # root load different index sets. That made the fused relevance, and
        # therefore this test, a function of ambient data: it passed from one
        # cwd and failed from the other on the same tree. The vector fan-out
        # and merge is what this test is about; hybrid fusion has its own
        # coverage in the BM25/fusion tests.
        with patch("core.retrieval.bm25.is_available", return_value=False):
            results = await multi_domain_query(
                "How does API rate limiting affect our systems?",
                domains=["coding", "finance"],
                chroma_client=client,
            )

        # Real assertions: the function fanned out to both collections, tagged
        # each result with the domain it came from, and merged them.
        queried = {c.kwargs.get("name") or c.args[0]
                   for c in client.get_collection.call_args_list}
        assert queried == {"domain_coding", "domain_finance"}, (
            f"must query both domain collections, queried {queried}"
        )
        domains_found = {r["domain"] for r in results}
        assert domains_found == {"coding", "finance"}

        # Relevance must be derived from the L2 distance, so the nearer chunk
        # scores higher. Asserted by domain rather than by position on purpose:
        # multi_domain_query MERGES but does not order — it returns
        # `all_results` straight out of the per-domain gather, so position
        # follows the `domains` argument. Sorting is the caller's job
        # (query_knowledge_base does `results.sort(...)` after dedup). An
        # earlier draft of this test asserted results[0] was the most relevant
        # and passed only because "coding" happened to be both first in the
        # domain list and the nearer hit.
        by_domain = {r["domain"]: r for r in results}
        assert by_domain["coding"]["relevance"] > by_domain["finance"]["relevance"], (
            "L2 distance 0.30 must map to a higher relevance than 0.45"
        )
        assert by_domain["coding"]["artifact_id"] == "art-c1"
        assert by_domain["finance"]["artifact_id"] == "art-f1"

    @pytest.mark.asyncio
    async def test_memory_extraction_from_chat(self):
        """Chat produces memory artifacts via the memory agent."""
        from app.agents.memory import extract_memories

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
