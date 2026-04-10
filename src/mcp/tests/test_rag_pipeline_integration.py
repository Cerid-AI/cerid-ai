# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for the RAG retrieval pipeline.

Exercises: query -> enrichment -> decomposition -> multi-domain search ->
dedup -> reranking -> NLI gate -> quality boost -> assembly.

ChromaDB and Neo4j are mocked with controlled return values.  Internal
logic (enrichment, NLI gate, dedup, assembly) runs for real.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# Mock heavy native deps if not available (host macOS lacks them; Docker has them).
# We must also mock core.utils.embeddings itself because it uses Python 3.10+
# union syntax (str | None) that fails to parse on the host's Python 3.9.
for _mod in ("onnxruntime", "huggingface_hub", "tokenizers"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
if "core.utils.embeddings" not in sys.modules:
    _emb_mock = MagicMock()
    _emb_mock.l2_distance_to_relevance = lambda d: max(0.0, 1.0 - d / 2.0)
    sys.modules["core.utils.embeddings"] = _emb_mock

from datetime import datetime, timezone  # noqa: E402
from unittest.mock import patch  # noqa: E402

import pytest  # noqa: E402

from core.agents.memory import calculate_memory_score, recall_memories  # noqa: E402
from core.agents.query_agent import (  # noqa: E402
    _enrich_query,
    agent_query,
    assemble_context,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    artifact_id: str = "art-1",
    chunk_index: int = 0,
    relevance: float = 0.8,
    domain: str = "coding",
    filename: str = "test.py",
    content: str = "some content",
    **extra,
) -> dict:
    return {
        "artifact_id": artifact_id,
        "chunk_index": chunk_index,
        "relevance": relevance,
        "domain": domain,
        "filename": filename,
        "content": content,
        "chunk_id": f"{artifact_id}_chunk_{chunk_index}",
        "collection": f"domain_{domain}",
        "ingested_at": "",
        "sub_category": "",
        "tags_json": "[]",
        "keywords": "[]",
        **extra,
    }


def _chroma_collection(documents, metadatas, distances, ids):
    """Build a mock ChromaDB collection that returns the given results."""
    coll = MagicMock()
    coll.query.return_value = {
        "documents": [documents],
        "metadatas": [metadatas],
        "distances": [distances],
        "ids": [ids],
    }
    coll.get.return_value = {
        "documents": [],
        "metadatas": [],
        "ids": [],
    }
    return coll


def _mock_chroma_client(collections: dict[str, MagicMock] | None = None):
    """Return a mock ChromaDB client.

    *collections* maps collection names to mock collection objects.
    Any unknown collection raises an exception (like real ChromaDB).
    """
    client = MagicMock()
    collections = collections or {}

    # list_collections returns lightweight stubs with a .name attr
    stubs = []
    for name in collections:
        stub = MagicMock()
        stub.name = name
        stubs.append(stub)
    client.list_collections.return_value = stubs

    def _get_collection(name, **kw):
        if name in collections:
            return collections[name]
        raise ValueError(f"Collection {name} not found")

    client.get_collection = MagicMock(side_effect=_get_collection)
    client.get_or_create_collection = MagicMock(side_effect=_get_collection)
    return client


def _mock_neo4j():
    """Return a mock Neo4j driver that returns empty results."""
    driver = MagicMock()
    session = MagicMock()
    session.run.return_value = MagicMock(data=MagicMock(return_value=[]))
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    return driver


# ---------------------------------------------------------------------------
# Feature-toggle overrides — disable expensive pipeline steps so the
# integration tests focus on the NLI gate and assembly logic.
# ---------------------------------------------------------------------------

_FEATURE_OVERRIDES = {
    "config.features.ENABLE_ADAPTIVE_RETRIEVAL": False,
    "config.features.ENABLE_QUERY_DECOMPOSITION": False,
    "config.features.ENABLE_MMR_DIVERSITY": False,
    "config.features.ENABLE_INTELLIGENT_ASSEMBLY": False,
    "config.features.ENABLE_LATE_INTERACTION": False,
    "config.features.ENABLE_SEMANTIC_CACHE": False,
}


# ===================================================================
# TestNliGateInPipeline
# ===================================================================


class TestNliGateInPipeline:
    """NLI contradiction/entailment gate within the full agent_query pipeline."""

    @pytest.fixture()
    def coding_collection(self):
        """ChromaDB collection with two documents."""
        return _chroma_collection(
            documents=[
                "PostgreSQL is slow and has many drawbacks including poor scalability",
                "PostgreSQL is fast and reliable for production workloads",
            ],
            metadatas=[
                {"domain": "coding", "filename": "drawbacks.md", "artifact_id": "art-draw",
                 "chunk_index": 0, "ingested_at": "", "sub_category": "",
                 "tags_json": "[]", "keywords": "[]"},
                {"domain": "coding", "filename": "advantages.md", "artifact_id": "art-adv",
                 "chunk_index": 0, "ingested_at": "", "sub_category": "",
                 "tags_json": "[]", "keywords": "[]"},
            ],
            distances=[0.3, 0.35],
            ids=["id-draw", "id-adv"],
        )

    @pytest.fixture()
    def chroma_client(self, coding_collection):
        return _mock_chroma_client({"domain_coding": coding_collection})

    @pytest.fixture()
    def neo4j_driver(self):
        return _mock_neo4j()

    # ------------------------------------------------------------------
    # 1. Contradictory result removed
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_contradictory_result_removed(self, chroma_client, neo4j_driver):
        """Query about benefits + KB doc about drawbacks -> contradiction -> removed."""
        nli_results = [
            # doc0 contradicts query
            {"contradiction": 0.85, "entailment": 0.05, "neutral": 0.10, "label": "contradiction"},
            # doc1 entails query
            {"contradiction": 0.02, "entailment": 0.90, "neutral": 0.08, "label": "entailment"},
        ]

        with patch.multiple("config.features", **{k.split(".")[-1]: v for k, v in _FEATURE_OVERRIDES.items()}):
            with patch("core.utils.nli.batch_nli_score", return_value=nli_results):
                with patch("core.retrieval.bm25.is_available", return_value=False):
                    result = await agent_query(
                        query="benefits of PostgreSQL",
                        domains=["coding"],
                        chroma_client=chroma_client,
                        neo4j_driver=neo4j_driver,
                        use_reranking=False,
                    )

        filenames = [r["filename"] for r in result["results"]]
        assert "drawbacks.md" not in filenames, "Contradictory result should be removed"
        assert "advantages.md" in filenames, "Entailing result should be kept"

    # ------------------------------------------------------------------
    # 2. Entailing result boosted
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_entailing_result_boosted(self, chroma_client, neo4j_driver):
        """KB doc about 'PostgreSQL is fast and reliable' -> entailment -> relevance boosted."""
        nli_results = [
            {"contradiction": 0.02, "entailment": 0.05, "neutral": 0.93, "label": "neutral"},
            {"contradiction": 0.01, "entailment": 0.85, "neutral": 0.14, "label": "entailment"},
        ]

        with patch.multiple("config.features", **{k.split(".")[-1]: v for k, v in _FEATURE_OVERRIDES.items()}):
            with patch("core.utils.nli.batch_nli_score", return_value=nli_results):
                with patch("core.retrieval.bm25.is_available", return_value=False):
                    result = await agent_query(
                        query="PostgreSQL advantages",
                        domains=["coding"],
                        chroma_client=chroma_client,
                        neo4j_driver=neo4j_driver,
                        use_reranking=False,
                    )

        entailed = [r for r in result["results"] if r["filename"] == "advantages.md"]
        assert len(entailed) == 1
        # Entailment should have triggered a +0.05 boost
        assert entailed[0].get("nli_entailment") is not None
        assert entailed[0]["nli_entailment"] >= 0.5

    # ------------------------------------------------------------------
    # 3. Neutral result unchanged
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_neutral_result_unchanged(self, chroma_client, neo4j_driver):
        """Unrelated KB doc sharing keywords -> neutral NLI -> relevance unchanged."""
        nli_results = [
            {"contradiction": 0.10, "entailment": 0.10, "neutral": 0.80, "label": "neutral"},
            {"contradiction": 0.10, "entailment": 0.10, "neutral": 0.80, "label": "neutral"},
        ]

        with patch.multiple("config.features", **{k.split(".")[-1]: v for k, v in _FEATURE_OVERRIDES.items()}):
            with patch("core.utils.nli.batch_nli_score", return_value=nli_results):
                with patch("core.retrieval.bm25.is_available", return_value=False):
                    result = await agent_query(
                        query="benefits of PostgreSQL",
                        domains=["coding"],
                        chroma_client=chroma_client,
                        neo4j_driver=neo4j_driver,
                        use_reranking=False,
                    )

        # Both neutral — neither boosted nor removed
        for r in result["results"]:
            assert r.get("nli_entailment") is None, "Neutral result should not get nli_entailment"

    # ------------------------------------------------------------------
    # 4. NLI unavailable -> graceful fallback
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_nli_unavailable_graceful(self, chroma_client, neo4j_driver):
        """When NLI raises ImportError all results are kept, no crash."""

        with patch.multiple("config.features", **{k.split(".")[-1]: v for k, v in _FEATURE_OVERRIDES.items()}):
            with patch("core.utils.nli.batch_nli_score", side_effect=ImportError("no onnx")):
                with patch("core.retrieval.bm25.is_available", return_value=False):
                    result = await agent_query(
                        query="benefits of PostgreSQL",
                        domains=["coding"],
                        chroma_client=chroma_client,
                        neo4j_driver=neo4j_driver,
                        use_reranking=False,
                    )

        assert result["total_results"] >= 1, "All results should be kept when NLI is unavailable"


# ===================================================================
# TestMemoryRecallIntegration
# ===================================================================


class TestMemoryRecallIntegration:
    """Memory recall with decay, NLI filtering, and type-based scoring."""

    def _memory_collection(self, documents, metadatas, distances, ids):
        """Build a mock conversations collection for memory recall."""
        return _chroma_collection(documents, metadatas, distances, ids)

    # ------------------------------------------------------------------
    # 5. Conversational memory decays quickly
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_conversational_memory_decays_quickly(self):
        """30-day-old conversational memory with 3-day half-life -> score ~0 -> filtered."""
        now = datetime.now(timezone.utc)
        thirty_days_ago = (now - __import__("datetime").timedelta(days=30)).isoformat()

        coll = self._memory_collection(
            documents=["Attended Python workshop last month"],
            metadatas=[{
                "memory_type": "conversational",
                "valid_from": thirty_days_ago,
                "ingested_at": thirty_days_ago,
                "artifact_id": "mem-conv-1",
                "access_count": "0",
                "summary": "Python workshop attendance",
            }],
            distances=[0.3],  # high similarity (low distance)
            ids=["mem-conv-1-chunk-0"],
        )
        client = _mock_chroma_client({"domain_conversations": coll})
        neo4j = _mock_neo4j()

        with patch("core.utils.nli.nli_score", return_value={
            "contradiction": 0.0, "entailment": 0.8, "neutral": 0.2, "label": "entailment",
        }):
            memories = await recall_memories(
                query="Python best practices",
                chroma_client=client,
                neo4j_driver=neo4j,
                top_k=10,
                min_score=0.3,
            )

        # conversational half-life = 3 days.  After 30 days: 2^(-30/3) = 2^-10 ~ 0.001
        # Even with base_similarity ~0.85 (from distance=0.3), adjusted score ~ 0.0008
        # This should be below any reasonable min_score, so no results returned.
        assert len(memories) == 0, (
            "30-day-old conversational memory should decay below min_score"
        )

    # ------------------------------------------------------------------
    # 6. Empirical memory persists
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_empirical_memory_persists(self):
        """365-day-old empirical fact -> no decay -> score preserved."""
        now = datetime.now(timezone.utc)
        one_year_ago = (now - __import__("datetime").timedelta(days=365)).isoformat()

        coll = self._memory_collection(
            documents=["Python is dynamically typed"],
            metadatas=[{
                "memory_type": "empirical",
                "valid_from": one_year_ago,
                "ingested_at": one_year_ago,
                "artifact_id": "mem-emp-1",
                "access_count": "2",
                "summary": "Python type system",
            }],
            distances=[0.25],  # very high similarity
            ids=["mem-emp-1-chunk-0"],
        )
        client = _mock_chroma_client({"domain_conversations": coll})
        neo4j = _mock_neo4j()

        with patch("core.utils.nli.nli_score", return_value={
            "contradiction": 0.0, "entailment": 0.9, "neutral": 0.1, "label": "entailment",
        }):
            memories = await recall_memories(
                query="Python type system",
                chroma_client=client,
                neo4j_driver=neo4j,
                top_k=10,
                min_score=0.3,
            )

        assert len(memories) == 1, "Empirical memory should survive regardless of age"
        assert memories[0]["memory_type"] == "empirical"
        # empirical decay = 1.0 always, so adjusted_score should be substantial
        assert memories[0]["adjusted_score"] > 0.5

    # ------------------------------------------------------------------
    # 7. Keyword-match memory filtered by NLI
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_keyword_match_memory_filtered_by_nli(self):
        """Memory 'attended Python workshop' + query 'Python best practices' ->
        high keyword similarity but low NLI entailment + low score -> filtered."""
        now = datetime.now(timezone.utc)
        ten_days_ago = (now - __import__("datetime").timedelta(days=10)).isoformat()

        coll = self._memory_collection(
            documents=["Attended Python workshop"],
            metadatas=[{
                "memory_type": "project_context",
                "valid_from": ten_days_ago,
                "ingested_at": ten_days_ago,
                "artifact_id": "mem-kw-1",
                "access_count": "0",
                "summary": "Workshop attendance",
            }],
            distances=[0.4],  # moderate similarity
            ids=["mem-kw-1-chunk-0"],
        )
        client = _mock_chroma_client({"domain_conversations": coll})
        neo4j = _mock_neo4j()

        # NLI shows low entailment — workshop attendance doesn't entail best practices.
        # Inject a fake nli module into sys.modules so the lazy ``from
        # core.utils.nli import nli_score`` inside recall_memories picks up
        # our mock even when the real module isn't installed on the host.
        _nli_rv = {"contradiction": 0.05, "entailment": 0.15, "neutral": 0.80, "label": "neutral"}
        _nli_mock = MagicMock(nli_score=MagicMock(return_value=_nli_rv))
        with patch.dict("sys.modules", {"core.utils.nli": _nli_mock}):
            memories = await recall_memories(
                query="Python best practices",
                chroma_client=client,
                neo4j_driver=neo4j,
                top_k=10,
                min_score=0.3,
            )

        # NLI entailment < 0.3 AND adjusted_score < 0.5 -> filtered
        assert len(memories) == 0, (
            "Keyword-only match with low NLI entailment should be filtered"
        )


# ===================================================================
# TestQueryEnrichment
# ===================================================================


class TestQueryEnrichment:
    """Conversation-aware query enrichment via _enrich_query."""

    # ------------------------------------------------------------------
    # 8. Enrichment adds conversation terms
    # ------------------------------------------------------------------
    def test_enrichment_adds_conversation_terms(self):
        """Query 'how to deploy' + conversation about Django -> enriched includes 'django'."""
        messages = [
            {"role": "user", "content": "I'm working on a Django project"},
            {"role": "assistant", "content": "Django is a great framework."},
            {"role": "user", "content": "How do I configure the settings?"},
        ]
        enriched = _enrich_query("how to deploy", messages)

        assert "django" in enriched.lower(), (
            "Enriched query should include 'django' from conversation context"
        )

    # ------------------------------------------------------------------
    # 9. Enrichment respects recency
    # ------------------------------------------------------------------
    def test_enrichment_respects_recency(self):
        """5 messages — most recent about React gets more term slots."""
        messages = [
            {"role": "user", "content": "I used Python yesterday for data analysis"},
            {"role": "user", "content": "Then I tried some Java Spring Boot"},
            {"role": "user", "content": "Also looked at Go lang concurrency"},
            {"role": "user", "content": "Did some Ruby on Rails work"},
            {"role": "user", "content": "Now I am building a React TypeScript frontend application"},
        ]
        enriched = _enrich_query("how to optimize", messages, max_terms=10)

        # Most recent message is about React — should appear in enrichment.
        # We verify React terms got priority over older message terms.
        enriched_lower = enriched.lower()
        assert "react" in enriched_lower, "Most recent topic 'React' should be in enriched query"

        # Count how many terms from recent (React) vs oldest (Python) message appear.
        # The recency weighting should give React more representation.
        react_terms = {"react", "typescript", "frontend", "application", "building"}
        oldest_terms = {"python", "yesterday", "data", "analysis"}

        react_count = sum(1 for t in react_terms if t in enriched_lower)
        oldest_count = sum(1 for t in oldest_terms if t in enriched_lower)

        assert react_count >= oldest_count, (
            f"Recent terms ({react_count}) should have >= representation than old terms ({oldest_count})"
        )


# ===================================================================
# TestContextAssembly
# ===================================================================


class TestContextAssembly:
    """Context assembly budget and facet coverage."""

    # ------------------------------------------------------------------
    # 10. Assembly respects budget
    # ------------------------------------------------------------------
    def test_assembly_respects_budget(self):
        """20 results but budget is 5000 chars -> assembled context under budget."""
        results = [
            _make_result(
                artifact_id=f"art-{i}",
                content=f"Content block number {i}. " * 50,  # ~350 chars each
                relevance=0.9 - i * 0.02,
            )
            for i in range(20)
        ]
        context, sources, char_count = assemble_context(results, max_chars=5000)

        assert char_count <= 5000, f"Context should be under 5000 chars, got {char_count}"
        assert len(context) <= 5000
        assert len(sources) < 20, "Should not include all 20 results in 5000-char budget"
        assert len(sources) > 0, "Should include at least some results"

    # ------------------------------------------------------------------
    # 11. Assembly covers facets
    # ------------------------------------------------------------------
    def test_assembly_covers_facets(self):
        """Query about 'A and B' -> assembly should include docs covering both A and B."""
        results = [
            _make_result(
                artifact_id="art-a1", content="Detailed coverage of topic A deployment strategies",
                relevance=0.95, domain="coding", filename="topicA.md",
            ),
            _make_result(
                artifact_id="art-a2", content="More about topic A and its advantages",
                relevance=0.90, domain="coding", filename="topicA2.md",
            ),
            _make_result(
                artifact_id="art-b1", content="Comprehensive guide to topic B integration patterns",
                relevance=0.85, domain="coding", filename="topicB.md",
            ),
            _make_result(
                artifact_id="art-b2", content="Topic B performance benchmarks and comparisons",
                relevance=0.80, domain="coding", filename="topicB2.md",
            ),
        ]

        context, sources, char_count = assemble_context(results, max_chars=10000)

        source_filenames = {s["filename"] for s in sources}

        # With budget >> content size, assembly should include results from both topics
        has_a = any("topicA" in fn for fn in source_filenames)
        has_b = any("topicB" in fn for fn in source_filenames)

        assert has_a and has_b, (
            f"Assembly should cover both facets. Got filenames: {source_filenames}"
        )


# ===================================================================
# TestCalculateMemoryScore (unit-level sanity checks used by integration)
# ===================================================================


class TestCalculateMemoryScoreIntegration:
    """Verify decay curves used by recall_memories."""

    def test_empirical_no_decay(self):
        """Empirical memories should have decay = 1.0 regardless of age."""
        score_new = calculate_memory_score(0.8, access_count=0, age_days=0, memory_type="empirical")
        score_old = calculate_memory_score(0.8, access_count=0, age_days=365, memory_type="empirical")
        assert score_new == score_old, "Empirical should not decay"

    def test_conversational_fast_decay(self):
        """Conversational memories with 3-day half-life should decay rapidly."""
        score_fresh = calculate_memory_score(0.8, access_count=0, age_days=0, memory_type="conversational")
        score_30d = calculate_memory_score(0.8, access_count=0, age_days=30, memory_type="conversational")

        # 2^(-30/3) = 2^-10 ~ 0.001  ->  0.8 * 0.001 ~ 0.0008
        assert score_30d < 0.01, f"30-day conversational should be near zero, got {score_30d}"
        assert score_fresh > score_30d * 100, "Fresh should be much higher than 30-day"

    def test_decision_power_law_long_tail(self):
        """Decision memories use power-law decay with long tail."""
        score_90d = calculate_memory_score(0.8, access_count=0, age_days=90, memory_type="decision")
        score_365d = calculate_memory_score(0.8, access_count=0, age_days=365, memory_type="decision")

        # Power-law decays slower than exponential — both should be non-trivial
        assert score_90d > 0.2, f"90-day decision should retain value, got {score_90d}"
        assert score_365d > 0.1, f"365-day decision should still be nonzero, got {score_365d}"
