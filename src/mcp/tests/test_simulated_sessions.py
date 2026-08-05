# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Simulated user session tests — full data flows with realistic multi-turn interactions.

All heavy dependencies (chromadb, neo4j, redis, tiktoken, httpx, spacy, etc.)
are pre-stubbed by conftest.py ``pytest_configure()``.

Mocking strategy: patch only at genuine I/O boundaries — the ChromaDB client,
the Neo4j driver, Redis, and the LLM client — then drive the **real**
production entry points (``agent_query``, ``ingest_content``,
``extract_memories``, and the verification calibrators) so every assertion
lands on a value production computed rather than one the test supplied.

Concretely: a test never patches the function it is testing. Retrieval tests
feed rows into a fake Chroma collection and assert on what the real pipeline
did with them — the L2-distance-to-relevance transform, domain routing,
context assembly, source provenance, and the envelope shape. Verification
tests feed KB snippets into the real numeric/temporal alignment calibrators
and assert on the verdicts those functions derive.

Realistic session data is modelled on tests/fixtures/synthetic/manifest.json.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config
from app.services.ingestion import ingest_content
from core.agents.hallucination.verification import (
    _build_verification_details,
    _check_numeric_alignment,
    _compute_adjusted_confidence,
    _verify_fact_relationship,
    verify_claim,
)
from core.agents.query_agent import _enrich_query, agent_query

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


# --- Retrieval harness ------------------------------------------------------
#
# The only thing faked below is the ChromaDB client itself (a network
# boundary). Everything downstream of it — distance scoring, domain fan-out,
# dedup, context assembly, envelope construction — is the real pipeline.

# Retrieval-shaping features are pinned off so a session test measures the
# base pipeline deterministically rather than whichever flags the host env
# happens to carry. Each is an independent code path with its own tests.
_FEATURE_OVERRIDES = {
    "ENABLE_ADAPTIVE_RETRIEVAL": False,
    "ENABLE_QUERY_DECOMPOSITION": False,
    "ENABLE_MMR_DIVERSITY": False,
    "ENABLE_INTELLIGENT_ASSEMBLY": False,
    "ENABLE_LATE_INTERACTION": False,
    "ENABLE_SEMANTIC_CACHE": False,
}


def _kb_metadata(artifact_id: str, filename: str, domain: str = "coding", **extra):
    """Metadata as the ingestion pipeline writes it onto a Chroma chunk."""
    meta = {
        "domain": domain,
        "filename": filename,
        "artifact_id": artifact_id,
        "chunk_index": 0,
        "ingested_at": "",
        "sub_category": "",
        "tags_json": "[]",
        "keywords": "[]",
    }
    meta.update(extra)
    return meta


def _fake_chroma(rows_by_domain: dict[str, list[tuple]]):
    """Build a fake Chroma client from ``{domain: [(id, distance, doc, meta)]}``.

    Mirrors real ChromaDB semantics that the retrieval path depends on:
    ``list_collections()`` advertises only the collections that exist, and
    ``get_collection`` raises for anything else.

    Returns ``(client, collections_by_name)`` so tests can assert on how the
    production code queried the store.
    """
    client = MagicMock()
    collections: dict[str, MagicMock] = {}
    stubs = []
    for domain, rows in rows_by_domain.items():
        name = config.collection_name(domain)
        col = MagicMock()
        col.query.return_value = {
            "ids": [[r[0] for r in rows]],
            "distances": [[r[1] for r in rows]],
            "documents": [[r[2] for r in rows]],
            "metadatas": [[r[3] for r in rows]],
        }
        col.get.return_value = {"documents": [], "metadatas": [], "ids": []}
        collections[name] = col
        stub = MagicMock()
        stub.name = name
        stubs.append(stub)
    client.list_collections.return_value = stubs

    def _get_collection(name, **kwargs):
        if name in collections:
            return collections[name]
        raise ValueError(f"Collection {name} not found")

    client.get_collection = MagicMock(side_effect=_get_collection)
    return client, collections


def _neutral_nli(pairs):
    """Neutral verdict per pair — the NLI gate neither drops nor boosts."""
    return [{"contradiction": 0.0, "entailment": 0.0, "neutral": 1.0,
             "label": "neutral"} for _ in pairs]


async def _run_query(chroma_client, query: str, **kwargs):
    """Drive the real ``agent_query`` against a fake Chroma client.

    BM25 is disabled so relevance reflects the vector arm's documented
    ``1 - d^2/2`` transform rather than a host-dependent on-disk keyword
    index; the DeBERTa NLI gate is pinned neutral so scores don't depend on
    a downloaded model (it has its own tests, and its scoring bonus is
    exercised deliberately in ``test_nli_entailment_boost_stays_within_relevance_bound``);
    ``log_event`` is silenced because it writes to Redis.
    """
    with _pinned_pipeline():
        return await _run_query_pinned(chroma_client, query, **kwargs)


@contextlib.contextmanager
def _pinned_pipeline():
    """Pin the retrieval-shaping flags and the model-backed arms.

    MUST NOT be entered concurrently by several coroutines. ``mock.patch`` is
    not concurrency-safe: each entrant records the attribute's *current* value
    as the one to restore, so a second coroutine entering while the first holds
    the patch records the already-patched value and writes THAT back on exit.
    The flags then leak out of the module as ``False``.

    That is not hypothetical — it happened here. Five concurrent ``_run_query``
    calls in ``test_rapid_sequential_queries`` leaked all five feature flags,
    and the damage landed on an unrelated test three files away
    (``test_kb_batch_preresolves_plain_factual_from_kb`` resolved via
    ``cross_model`` instead of ``kb_batch``) — passing alone, failing in the
    suite. Concurrent callers enter this ONCE around the gather and use
    ``_run_query_pinned``.
    """
    with patch.multiple("config.features", **_FEATURE_OVERRIDES), \
         patch("core.retrieval.bm25.is_available", return_value=False), \
         patch("core.utils.nli.batch_nli_score", side_effect=_neutral_nli), \
         patch("core.agents.query_agent.log_event"):
        yield


async def _run_query_pinned(chroma_client, query: str, **kwargs):
    """``_run_query`` for callers already inside ``_pinned_pipeline()``."""
    return await agent_query(
        query=query,
        chroma_client=chroma_client,
        neo4j_driver=MagicMock(),
        redis_client=None,
        use_reranking=False,
        **kwargs,
    )


def _queried_collections(chroma_client) -> list[str]:
    """Collection names the production path actually opened."""
    return [c.args[0] if c.args else c.kwargs["name"]
            for c in chroma_client.get_collection.call_args_list]


# --- Verification harness ---------------------------------------------------
#
# Patched: the KB retrieval arm, the memory lookup, the NLI model, the external
# cross-model verifier, and the verdict cache — every one an I/O boundary.
# The verification logic itself (grounding, numeric/temporal calibration,
# escalation routing, verdict assembly) runs for real.

_NEUTRAL_EXTERNAL = {
    "status": "uncertain", "confidence": 0.3, "reason": "no decisive evidence",
}


def _nli(entailment: float = 0.0, contradiction: float = 0.0,
         neutral: float = 1.0, label: str = "neutral"):
    return {"entailment": entailment, "contradiction": contradiction,
            "neutral": neutral, "label": label}


def _kb_evidence(content: str, relevance: float = 0.9, domain: str = "general",
                 artifact_id: str = "art-kb", filename: str = "kb.md"):
    """A KB hit shaped as ``lightweight_kb_query`` returns it."""
    return [{
        "content": content, "relevance": relevance, "domain": domain,
        "artifact_id": artifact_id, "filename": filename, "memory_source": False,
    }]


async def _verify(claim: str, kb_results: list[dict], nli: dict,
                  external: dict | None = None):
    """Drive the real ``verify_claim``. Returns ``(verdict, external_mock)``."""
    ext_mock = AsyncMock(return_value=external or _NEUTRAL_EXTERNAL)
    with patch("core.agents.query_agent.lightweight_kb_query",
               new=AsyncMock(return_value=kb_results)), \
         patch("core.agents.hallucination.verification._query_memories",
               new=AsyncMock(return_value=[])), \
         patch("core.utils.nli.nli_score_async", new=AsyncMock(return_value=nli)), \
         patch("core.agents.hallucination.verification._verify_claim_externally",
               new=ext_mock), \
         patch("core.agents.hallucination.verification.get_cached_verdict",
               new=AsyncMock(return_value=None)), \
         patch("core.agents.hallucination.verification.cache_verdict",
               new=AsyncMock(return_value=None)):
        verdict = await verify_claim(claim, MagicMock(), None, MagicMock())
    return verdict, ext_mock


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
        """Real ``_enrich_query`` folds prior turns into each new query.

        The session value is that turn N's retrieval query is *not* what the
        user typed — it carries recency-weighted terms from earlier turns, so
        "What about the migration plan?" can still retrieve database material.
        """
        conversation_messages: list[dict[str, str]] = []
        enriched_per_turn = []

        turns = [
            ("What database did we choose?", "We chose PostgreSQL 15 for the project."),
            ("Why did we reject MongoDB?", "MongoDB lacked strong transactional guarantees."),
            ("What about the migration plan?", "Migration uses Alembic and PgBouncer."),
        ]

        for question, answer in turns:
            enriched_per_turn.append(
                _enrich_query(question, list(conversation_messages))
            )
            conversation_messages.append({"role": "user", "content": question})
            conversation_messages.append({"role": "assistant", "content": answer})

        # Turn 1 has no prior context — production returns the query untouched.
        assert enriched_per_turn[0] == "What database did we choose?"

        # Turn 2 inherits turn 1's terms.
        assert enriched_per_turn[1].startswith("Why did we reject MongoDB?")
        assert "database" in enriched_per_turn[1]

        # Turn 3 inherits both prior turns, and the enrichment strictly grows.
        turn3 = enriched_per_turn[2]
        assert turn3.startswith("What about the migration plan?")
        assert "mongodb" in turn3 and "database" in turn3
        assert len(turn3.split()) > len(enriched_per_turn[1].split())

        # Recency weighting: turn 2's terms are emitted before turn 1's.
        assert turn3.index("mongodb") < turn3.index("database")

        # Assistant turns are never mined — only user messages contribute.
        assert "alembic" not in turn3 and "pgbouncer" not in turn3
        assert "postgresql" not in turn3.lower().split("?")[-1]

        # Session state itself accumulated two messages per turn.
        assert len(conversation_messages) == 6

    @pytest.mark.asyncio
    async def test_kb_injection_persists_across_turns(self):
        """One ingested doc grounds two sequential queries in the same session."""
        kb_text = "PostgreSQL uses MVCC for concurrent access"
        chroma, _ = _fake_chroma({
            "coding": [("chunk-mvcc", 0.2, kb_text, _kb_metadata("art-1", "postgres.md"))],
        })

        turn1 = await _run_query(chroma, "What is MVCC?", domains=["coding"])
        turn2 = await _run_query(
            chroma, "How does PostgreSQL handle concurrency?", domains=["coding"])

        for turn in (turn1, turn2):
            # Context was assembled by production from the Chroma document.
            assert turn["context"] == kb_text
            assert turn["total_results"] == 1
            # Relevance is the real L2 transform of distance 0.2: 1 - 0.2^2/2.
            assert turn["sources"][0]["relevance"] == 0.98
            # Provenance the pipeline stamped on, not present in the raw row.
            assert turn["sources"][0]["source_type"] == "kb"
            assert turn["sources"][0]["artifact_id"] == "art-1"

        # The KB was re-consulted per turn — nothing cached the first answer.
        assert chroma.get_collection.call_count == 2

    @pytest.mark.asyncio
    async def test_memory_extracted_then_recalled(self):
        """Turn 1 extracts memories from a response; turn 2 retrieves them from the KB."""
        response_text = (
            "We decided to use PostgreSQL 15 for the database. Key factors "
            "were ACID compliance and MVCC concurrency support. The migration "
            "plan includes Alembic for schema management and starts Monday."
        )
        llm_payload = [
            {"content": "Chose PostgreSQL 15 for database", "memory_type": "decision",
             "summary": "DB choice: Postgres 15"},
            {"content": "Migration starts Monday", "memory_type": "temporal",
             "summary": "Migration timeline"},
        ]

        # Real extraction; only the LLM transport is faked.
        with patch("core.agents.memory.call_internal_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = json.dumps(llm_payload)
            from app.agents.memory import extract_memories
            memories = await extract_memories(response_text, conversation_id="conv-session-1")

        assert len(memories) >= 1
        recalled = memories[0]["content"]
        assert "PostgreSQL" in recalled

        # Turn 2: that extracted memory is now KB content. Drive real retrieval
        # over it and confirm production surfaces it as a memory-class source.
        chroma, _ = _fake_chroma({
            "conversations": [(
                "chunk-mem", 0.3, recalled,
                _kb_metadata("mem-1", "memory_conv-session-1.md",
                             domain="conversations", memory_type="decision"),
            )],
        })
        turn2 = await _run_query(
            chroma, "What database are we using?", domains=["conversations"])

        assert turn2["total_results"] == 1
        assert "PostgreSQL" in turn2["context"]
        # The conversations-policy pass tags memory artifacts as user-authored
        # rather than dropping them the way it drops chat transcripts.
        assert turn2["results"][0]["source_authority"] == "user_memory"

    @pytest.mark.asyncio
    async def test_conversation_maintains_domain_focus(self):
        """A coding-scoped session never fans out to other domains' collections."""
        chroma, _ = _fake_chroma({
            "coding": [("c1", 0.25, "Python async/await patterns",
                        _kb_metadata("art-py", "async.md"))],
            "finance": [("f1", 0.25, "Quarterly revenue recognition",
                         _kb_metadata("art-fin", "q3.md", domain="finance"))],
        })

        turn1 = await _run_query(chroma, "Explain async patterns",
                                 domains=["coding"], strict_domains=True)
        turn2 = await _run_query(chroma, "How about error handling?",
                                 domains=["coding"], strict_domains=True)

        for turn in (turn1, turn2):
            assert turn["domains_searched"] == ["coding"]
            assert {s["domain"] for s in turn["sources"]} == {"coding"}
            assert "revenue" not in turn["context"]

        # Routing is enforced at the store boundary, not just in the envelope:
        # the finance collection was never opened.
        opened = set(_queried_collections(chroma))
        assert opened == {config.collection_name("coding")}

    @pytest.mark.asyncio
    async def test_model_switch_mid_conversation(self):
        """``agent_query`` forwards a per-turn model override to the impl."""
        envelope = {
            "context": "", "sources": [], "confidence": 0.0,
            "domains_searched": [], "total_results": 0, "results": [],
        }
        # Patch the *dependency* (_agent_query_impl) and call the real
        # agent_query, so the budget wrapper and kwarg forwarding are exercised.
        with patch("core.agents.query_agent._agent_query_impl",
                   new_callable=AsyncMock, return_value=envelope) as mock_impl:
            await agent_query("question 1", model="openai/gpt-4o",
                              chroma_client=MagicMock())
            await agent_query("question 2", model="anthropic/claude-sonnet-4",
                              chroma_client=MagicMock())

        assert mock_impl.call_count == 2
        # Production threaded each turn's model through to the implementation.
        assert mock_impl.call_args_list[0].kwargs["model"] == "openai/gpt-4o"
        assert mock_impl.call_args_list[1].kwargs["model"] == "anthropic/claude-sonnet-4"
        # ...and the query text stayed paired with its own turn.
        assert mock_impl.call_args_list[0].kwargs["query"] == "question 1"
        assert mock_impl.call_args_list[1].kwargs["query"] == "question 2"

    @pytest.mark.asyncio
    async def test_conversation_with_verification(self):
        """Query the KB, then verify the answer's claim against what was retrieved."""
        kb_text = "PostgreSQL uses MVCC, introduced in 1985, for concurrent access"
        chroma, _ = _fake_chroma({
            "coding": [("c1", 0.2, kb_text, _kb_metadata("art-1", "postgres.md"))],
        })

        query_result = await _run_query(chroma, "Tell me about PostgreSQL",
                                        domains=["coding"])
        assert "MVCC" in query_result["context"]

        # Feed the session's own retrieved evidence into the real verifier.
        claim = "PostgreSQL uses MVCC, introduced in 1985"
        verdict, ext = await _verify(
            claim, query_result["results"], _nli(entailment=0.93, label="entailment"))

        assert verdict["status"] == "verified"
        assert verdict["verification_method"] == "kb_nli"
        # Provenance links the verdict back to the artifact the session retrieved.
        assert verdict["source_artifact_id"] == "art-1"
        assert verdict["source_domain"] == "coding"
        # The year in the claim matches the retrieved snippet.
        assert verdict["verification_details"]["numeric_alignment"] == "match"
        # KB-verified claims skip the paid external verifier — the documented
        # contract, and the reason a grounded session is cheap.
        ext.assert_not_called()


# ===========================================================================
# 2. TestSyntheticKBInjection
# ===========================================================================

class TestSyntheticKBInjection:
    """Use manifest.json-style synthetic data to validate KB injection flows."""

    @pytest.mark.asyncio
    async def test_ingest_quantum_doc_verify_facts(self):
        """A science doc is retrievable and its complexity formula survives assembly."""
        kb_chunk = ("Shor's algorithm factors large integers in polynomial time "
                    "O((log N)^2 * (log log N) * (log log log N))")
        chroma, _ = _fake_chroma({
            "science": [("q1", 0.4, kb_chunk,
                         _kb_metadata("art-quantum", "quantum.md", domain="science"))],
        })

        result = await _run_query(chroma, "What is Shor's algorithm complexity?",
                                  domains=["science"])

        # Production assembled the context verbatim — punctuation-heavy content
        # is not mangled by chunk joining.
        assert "O((log N)^2" in result["context"]
        assert result["domains_searched"] == ["science"]
        # Relevance is the real transform of distance 0.4: 1 - 0.4^2/2 = 0.92.
        assert result["sources"][0]["relevance"] == 0.92
        assert result["confidence"] > 0

    @pytest.mark.asyncio
    async def test_ingest_financial_doc_verify_numbers(self):
        """Finance retrieval preserves exact figures and routes to the finance domain."""
        kb_chunk = "Meridian Technologies Q3 2025 total revenue was $847.3M, up 12.4% YoY"
        chroma, _ = _fake_chroma({
            "finance": [("f1", 0.3, kb_chunk,
                         _kb_metadata("art-fin", "meridian_q3.md", domain="finance"))],
        })

        result = await _run_query(chroma, "What was Meridian revenue?",
                                  domains=["finance"])

        assert "$847.3M" in result["context"]
        assert result["sources"][0]["domain"] == "finance"
        assert result["sources"][0]["filename"] == "meridian_q3.md"
        assert _queried_collections(chroma) == [config.collection_name("finance")]

    @pytest.mark.asyncio
    async def test_ingest_api_doc_verify_endpoints(self):
        """The highest-scoring chunk leads the assembled context."""
        rate_limit = "Professional tier rate limit is 1,000 requests per minute"
        free_tier = "Free tier rate limit is 60 requests per minute"
        chroma, _ = _fake_chroma({
            "coding": [
                # Deliberately supplied worst-first — production must reorder.
                ("api-free", 0.7, free_tier, _kb_metadata("art-api", "api.md",
                                                          chunk_index=1)),
                ("api-pro", 0.2, rate_limit, _kb_metadata("art-api", "api.md",
                                                          chunk_index=0)),
            ],
        })

        result = await _run_query(chroma, "What is the Professional rate limit?",
                                  domains=["coding"])

        assert "1,000" in result["context"]
        # Ranking is production's, not the input order's.
        assert result["context"].index(rate_limit) < result["context"].index(free_tier)
        assert result["results"][0]["chunk_id"] == "api-pro"
        assert result["results"][0]["relevance"] > result["results"][1]["relevance"]

    @pytest.mark.asyncio
    async def test_ingest_medical_doc_verify_stats(self):
        """Medical trial stats survive retrieval and pass the numeric alignment check."""
        kb_chunk = "Responder rate: 62% Nexoril vs 34% placebo (p < 0.001)"
        chroma, _ = _fake_chroma({
            "medical": [("m1", 0.3, kb_chunk,
                         _kb_metadata("art-med", "clarity7.md", domain="medical"))],
        })

        result = await _run_query(chroma, "What were the Nexoril results?",
                                  domains=["medical"])

        assert "62%" in result["context"]
        assert result["domains_searched"] == ["medical"]

        # The real verifier agrees the retrieved evidence supports the number.
        adj = _check_numeric_alignment("Nexoril showed a 62% responder rate",
                                       result["results"][0])
        assert adj > 0

    @pytest.mark.asyncio
    async def test_ingest_project_doc_verify_dates(self):
        """Two chunks of one artifact are both assembled, in relevance order."""
        sprint = "Sprint 14 ends April 15, 2026."
        backend = "Backend migration is 78% complete."
        chroma, _ = _fake_chroma({
            "general": [
                ("p1", 0.25, sprint, _kb_metadata("art-proj", "notes.md",
                                                  domain="general", chunk_index=0)),
                ("p2", 0.45, backend, _kb_metadata("art-proj", "notes.md",
                                                   domain="general", chunk_index=1)),
            ],
        })

        result = await _run_query(chroma, "When does Sprint 14 end?",
                                  domains=["general"])

        assert "April 15, 2026" in result["context"]
        assert result["total_results"] == 2
        # Same artifact, distinct chunks — dedup keys on (artifact_id, chunk_index)
        # and must not collapse them.
        assert {r["chunk_index"] for r in result["results"]} == {0, 1}
        # Production joined the chunks with the blank-line separator.
        assert result["context"] == f"{sprint}\n\n{backend}"

    @pytest.mark.asyncio
    async def test_mixed_claims_correct_facts_verified(self):
        """Correct claims align numerically with their KB evidence."""
        # (claim, supporting KB snippet)
        correct = [
            ("Python was created by Guido van Rossum and first released in 1991",
             "Python, created by Guido van Rossum, was first released in 1991."),
            ("JavaScript was created by Brendan Eich in 1995 at Netscape",
             "Brendan Eich wrote JavaScript at Netscape in 1995."),
            ("TCP/IP was formally adopted by ARPANET on January 1, 1983",
             "ARPANET formally adopted TCP/IP on January 1, 1983."),
            ("TLS 1.3 was published in August 2018 as RFC 8446",
             "RFC 8446 published TLS 1.3 in August 2018."),
            ("Hubble Space Telescope was launched on April 24, 1990",
             "Hubble launched aboard Discovery on April 24, 1990."),
            ("Alan Turing published 'On Computable Numbers' in 1936",
             "Turing's 'On Computable Numbers' appeared in 1936."),
            ("FFT was published by Cooley and Tukey in 1965",
             "Cooley and Tukey published the FFT in 1965."),
        ]

        for claim, evidence in correct:
            verdict, ext = await _verify(
                claim, _kb_evidence(evidence),
                _nli(entailment=0.93, label="entailment"))

            assert verdict["status"] == "verified", claim
            assert verdict["verification_method"] == "kb_nli", claim
            # Years in the claim match the evidence — the real calibrator agrees.
            assert verdict["verification_details"]["numeric_alignment"] == "match", claim
            # Calibration nudged similarity above the raw 0.9 retrieval score.
            assert verdict["similarity"] > 0.9, claim
            # Strong KB grounding means no external escalation.
            ext.assert_not_called()
            assert "kb_nli_escalated" not in verdict, claim

    @pytest.mark.asyncio
    async def test_mixed_claims_wrong_facts_detected(self):
        """Wrong dates/numbers are caught by the real alignment calibrators."""
        # (claim, contradicting KB snippet)
        wrong = [
            ("HTTP/2 was standardized in 2012 as RFC 7540",
             "HTTP/2 was standardized in 2015 as RFC 7540."),
            ("The Human Genome Project was declared complete in June 2000",
             "The Human Genome Project was declared complete in April 2003."),
            ("TCP/IP was formally adopted by ARPANET in 1973",
             "ARPANET formally adopted TCP/IP on January 1, 1983."),
        ]

        for claim, evidence in wrong:
            top = {"content": evidence, "relevance": 0.9, "domain": "general"}
            # Embedding similarity is high (0.9) yet production still penalises:
            # this is the inverted-fact defence the calibrator exists for.
            assert _check_numeric_alignment(claim, top) < 0, claim
            assert _build_verification_details(claim, [top])["numeric_alignment"] == "conflict"
            # Calibrated confidence lands strictly below the raw similarity.
            assert _compute_adjusted_confidence(claim, [top], 0.9) < 0.9, claim

            # End-to-end: the same wrong claim is escalated and rejected.
            verdict, ext = await _verify(
                claim, _kb_evidence(evidence),
                _nli(contradiction=0.88, neutral=0.1, label="contradiction"),
                external={"status": "unverified", "confidence": 0.2,
                          "reason": "contradicted by authoritative sources"})
            assert verdict["status"] == "unverified", claim
            # A KB contradiction escalates rather than silently accepting the
            # high embedding similarity.
            assert verdict["kb_nli_escalated"] is True, claim
            assert verdict["kb_nli_contradiction"] == 0.88, claim
            ext.assert_called_once()

        # The two checks have different granularity, and that distinction is
        # load-bearing: _verify_fact_relationship's temporal rule compares
        # *decades*, so only the 1973-vs-1983 error trips it. The same-decade
        # errors above are caught by _check_numeric_alignment alone.
        same_decade = {"content": "HTTP/2 was standardized in 2015 as RFC 7540.",
                       "relevance": 0.9, "domain": "general"}
        assert _verify_fact_relationship(
            "HTTP/2 was standardized in 2012 as RFC 7540", same_decade)["aligned"] is True

        cross_decade = {"content": "ARPANET formally adopted TCP/IP on January 1, 1983.",
                        "relevance": 0.9, "domain": "general"}
        rel = _verify_fact_relationship(
            "TCP/IP was formally adopted by ARPANET in 1973", cross_decade)
        assert rel["aligned"] is False
        assert "temporal_mismatch" in rel["reason"]

        # A percentage far outside the source's range is flagged too.
        pct_claim = "JWST primary mirror reflectivity is 8.2% of the incident light"
        pct_evidence = "The JWST primary mirror reflects 98.0% of incident light."
        pct_top = {"content": pct_evidence, "relevance": 0.9, "domain": "science"}
        rel = _verify_fact_relationship(pct_claim, pct_top)
        assert "percentage_gap" in rel["reason"]
        assert rel["confidence_adjustment"] < 0

    @pytest.mark.asyncio
    async def test_cross_domain_retrieval(self):
        """A query spanning two domains merges both collections into one ranking."""
        coding_text = "Python async API calls use aiohttp connection pools"
        finance_text = "API rate limiting affects trading latency budgets"
        chroma, _ = _fake_chroma({
            "coding": [("c1", 0.3, coding_text, _kb_metadata("art-c", "async.md"))],
            "finance": [("f1", 0.5, finance_text,
                         _kb_metadata("art-f", "latency.md", domain="finance"))],
        })

        result = await _run_query(chroma, "How does API rate limiting affect us?",
                                  domains=["coding", "finance"])

        domains_found = {s["domain"] for s in result["sources"]}
        assert domains_found == {"coding", "finance"}
        assert result["total_results"] == 2
        # Both collections were genuinely opened.
        assert set(_queried_collections(chroma)) == {
            config.collection_name("coding"), config.collection_name("finance")}
        # Cross-collection results are merged into a single relevance ranking:
        # coding (d=0.3 -> 0.955) outranks finance (d=0.5 -> 0.875).
        assert [r["domain"] for r in result["results"]] == ["coding", "finance"]
        assert result["results"][0]["relevance"] == 0.955
        assert result["results"][1]["relevance"] == 0.875
        # Domain diversity raises calibrated confidence in the real verifier.
        assert _compute_adjusted_confidence(
            "API rate limiting affects latency", result["results"], 0.9) > 0.9


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
        assert call_kwargs.kwargs.get("domain") == "coding"
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
        """An existing-but-empty collection yields a well-formed zero-result envelope."""
        chroma, _ = _fake_chroma({"coding": []})

        result = await _run_query(chroma, "What is anything?", domains=["coding"])

        # Production still opened the collection — the KB exists, it's just empty.
        assert _queried_collections(chroma) == [config.collection_name("coding")]
        assert result["context"] == ""
        assert result["confidence"] == 0.0
        assert result["total_results"] == 0
        assert result["sources"] == []
        # The envelope contract holds on the empty path too.
        assert {"context", "sources", "confidence", "domains_searched",
                "total_results", "token_budget_used", "graph_results",
                "results"}.issubset(result)
        assert result["token_budget_used"] == 0

    @pytest.mark.asyncio
    async def test_nli_entailment_boost_stays_within_relevance_bound(self):
        """Relevance must stay within [0, 1] after the NLI entailment bonus.

        Regression guard for a real bug this test found while it was being
        written. The NLI gate added its 0.05 entailment bonus unclamped, so a
        strong vector hit at 0.98 was published to API and UI consumers as
        **1.03** — outside the [0, 1] range that ``l2_distance_to_relevance``
        documents and enforces, and that the multiplier path in the same module
        already clamped. ``apply_metadata_boost``,
        ``apply_context_alignment_boost`` and the temporal-recency boost added
        unclamped in exactly the same way; all four now clamp.

        It hid for so long because the envelope's top-level ``confidence`` *is*
        clamped and stayed a plausible 1.0. Only the per-source ``relevance``
        leaked out of range, and nothing asserted on it.
        """
        chroma, _ = _fake_chroma({
            "coding": [("c1", 0.2, "PostgreSQL uses MVCC for concurrent access",
                        _kb_metadata("art-1", "postgres.md"))],
        })
        entailing = [{"contradiction": 0.0, "entailment": 0.9, "neutral": 0.1,
                      "label": "entailment"}]

        with patch.multiple("config.features", **_FEATURE_OVERRIDES), \
             patch("core.retrieval.bm25.is_available", return_value=False), \
             patch("core.utils.nli.batch_nli_score", return_value=entailing), \
             patch("core.agents.query_agent.log_event"):
            result = await agent_query(
                query="How does PostgreSQL handle concurrency?",
                domains=["coding"], chroma_client=chroma,
                neo4j_driver=MagicMock(), redis_client=None, use_reranking=False)

        relevance = result["sources"][0]["relevance"]
        # The bonus was applied on top of the 0.98 vector score.
        assert result["results"][0]["nli_entailment"] == 0.9
        assert relevance > 0.98
        assert relevance <= 1.0, (
            f"relevance {relevance} exceeds the documented [0,1] bound"
        )

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
            "Kanji: 機械学習 (Machine Learning). Emoji: \U0001f916\U0001f4ca✨.\n"
            "Mathematical: ∀x ∈ ℝ, f(x) = ∑ aᵢxⁱ.\n"
            "Arabic: التعلم الآلي. Cyrillic: Машинное обучение.\n"
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
        """5 concurrent queries each get their own correctly-grounded envelope."""
        chroma, _ = _fake_chroma({
            "coding": [("c1", 0.2, "Connection pooling reuses open sockets",
                        _kb_metadata("art-pool", "pool.md"))],
        })

        # Patch ONCE around the gather — see _pinned_pipeline's docstring for
        # why entering it from five concurrent coroutines corrupts the restore.
        with _pinned_pipeline():
            results = await asyncio.gather(*[
                _run_query_pinned(chroma, f"Query number {i}", domains=["coding"])
                for i in range(5)
            ])

        assert len(results) == 5
        for r in results:
            # Each concurrent run independently retrieved and assembled.
            assert r["total_results"] == 1
            assert r["context"] == "Connection pooling reuses open sockets"
            assert r["sources"][0]["relevance"] == 0.98
        # Five real round-trips to the store — no cross-task result sharing.
        assert chroma.get_collection.call_count == 5

    @pytest.mark.asyncio
    async def test_query_nonexistent_domain(self):
        """An unknown domain is skipped without ever hitting the store."""
        chroma, _ = _fake_chroma({
            "coding": [("c1", 0.2, "real content", _kb_metadata("art-1", "a.md"))],
        })

        result = await _run_query(chroma, "anything", domains=["nonexistent"],
                                  strict_domains=True)

        assert result["context"] == ""
        assert result["total_results"] == 0
        assert result["sources"] == []
        assert "nonexistent" in result["domains_searched"]
        # The optimisation that matters: the collection is absent from the
        # list_collections pre-check, so no round-trip is spent on it — and the
        # existing coding collection is not silently substituted.
        assert _queried_collections(chroma) == []

    @pytest.mark.asyncio
    async def test_unknown_domain_still_bleeds_into_adjacent_domains(self):
        """Without ``strict_domains``, an unknown domain still fans out to
        adjacent built-in domains — but their hits are affinity-discounted
        away rather than surfacing as answers for a domain that doesn't exist."""
        chroma, _ = _fake_chroma({
            "coding": [("c1", 0.2, "real content", _kb_metadata("art-1", "a.md"))],
        })

        result = await _run_query(chroma, "anything", domains=["nonexistent"])

        # The cross-domain bleed did open a real collection...
        assert config.collection_name("coding") in _queried_collections(chroma)
        # ...yet nothing survives into the answer for the unknown domain.
        assert result["total_results"] == 0
        assert result["context"] == ""


# ===========================================================================
# 5. TestVerificationWithKBData
# ===========================================================================

class TestVerificationWithKBData:
    """Verify claims against real retrieved KB content — match, contradiction,
    numeric precision, and the no-evidence path."""

    async def _retrieve(self, kb_text: str, query: str, domain: str = "general",
                        distance: float = 0.2):
        chroma, _ = _fake_chroma({
            domain: [("kb-1", distance, kb_text,
                      _kb_metadata("art-kb", "kb.md", domain=domain))],
        })
        return await _run_query(chroma, query, domains=[domain])

    @pytest.mark.asyncio
    async def test_claim_verified_against_kb_content(self):
        """A claim entailed by KB content the session retrieved is verified."""
        retrieved = await self._retrieve(
            "Python was created by Guido van Rossum and first released in 1991.",
            "Who created Python?")
        claim = "Python was created by Guido van Rossum in 1991"

        verdict, ext = await _verify(
            claim, retrieved["results"], _nli(entailment=0.94, label="entailment"))

        assert verdict["status"] == "verified"
        assert verdict["verification_method"] == "kb_nli"
        assert verdict["nli_entailment"] == 0.94
        # Retrieval scored 0.98 (d=0.2); the exact 1991 match adds +0.03 and the
        # single-result penalty subtracts 0.02, and the result is clamped to 1.0.
        assert verdict["similarity"] >= retrieved["results"][0]["relevance"]
        assert verdict["verification_details"]["numeric_alignment"] == "match"
        ext.assert_not_called()

    @pytest.mark.asyncio
    async def test_claim_contradicted_by_kb(self):
        """A claim whose figure contradicts the KB escalates and is rejected."""
        retrieved = await self._retrieve(
            "Meridian Technologies Q3 2025 revenue was $847.3M, up 12.4% YoY.",
            "What was Meridian revenue?", domain="finance")
        claim = "Meridian Q3 2025 revenue was $900M, up 90.0% year over year"
        top = retrieved["results"]

        # The pure calibrator flags the >20pp percentage gap...
        rel = _verify_fact_relationship(claim, top[0])
        assert "percentage_gap" in rel["reason"]
        assert rel["aligned"] is False
        assert _check_numeric_alignment(claim, top[0]) < 0

        # ...and end-to-end the verdict is unverified via escalation, despite
        # the retrieval similarity being high (0.98).
        verdict, ext = await _verify(
            claim, top, _nli(contradiction=0.9, neutral=0.05, label="contradiction"),
            external={"status": "unverified", "confidence": 0.15,
                      "reason": "figures contradict the filing"})

        assert verdict["status"] == "unverified"
        assert verdict["kb_nli_escalated"] is True
        assert verdict["similarity"] < top[0]["relevance"]
        ext.assert_called_once()

    @pytest.mark.asyncio
    async def test_numerical_claim_precision(self):
        """One piece of evidence verifies the exact figure and rejects a wrong one."""
        retrieved = await self._retrieve(
            "Responder rate: 62% Nexoril vs 34% placebo (p < 0.001).",
            "What was the Nexoril responder rate?", domain="medical")
        top = retrieved["results"]

        exact_verdict, exact_ext = await _verify(
            "Nexoril showed 62% responder rate", top,
            _nli(entailment=0.92, label="entailment"))
        wrong_verdict, wrong_ext = await _verify(
            "Nexoril showed 95% responder rate", top,
            _nli(contradiction=0.87, neutral=0.08, label="contradiction"),
            external={"status": "unverified", "confidence": 0.2,
                      "reason": "trial reported 62%, not 95%"})

        # Same evidence, opposite verdicts — the discrimination that makes
        # verification worth running at all.
        assert exact_verdict["status"] == "verified"
        assert wrong_verdict["status"] == "unverified"
        assert exact_verdict["similarity"] > wrong_verdict["similarity"]

        # The correct figure is settled from the KB alone; only the wrong one
        # costs an external call.
        exact_ext.assert_not_called()
        wrong_ext.assert_called_once()
        assert exact_verdict["verification_details"]["numeric_alignment"] == "match"

        # The pure calibrator draws the same line on the raw snippet.
        assert _check_numeric_alignment("Nexoril showed 62% responder rate", top[0]) > 0
        assert _check_numeric_alignment("Nexoril showed 95% responder rate", top[0]) < 0

    @pytest.mark.asyncio
    async def test_verification_with_no_kb_match(self):
        """A claim absent from the KB falls through to external verification."""
        chroma, _ = _fake_chroma({"general": []})
        retrieved = await _run_query(
            chroma, "How many people live in the Mars colony?", domains=["general"])

        # Nothing retrieved -> no KB grounding to reason from.
        assert retrieved["results"] == []
        assert retrieved["confidence"] == 0.0

        claim = "The population of the Mars colony is 50,000"
        verdict, ext = await _verify(
            claim, retrieved["results"], _nli(),
            external={"status": "uncertain", "confidence": 0.4,
                      "reason": "no reliable sources found",
                      "verification_method": "web_search"})

        # The empty-KB fallback fired, and the verdict carries the external
        # method rather than pretending to KB grounding.
        ext.assert_called_once()
        assert ext.call_args.args[0] == claim
        assert verdict["status"] == "uncertain"
        assert verdict["verification_method"] == "web_search"
        assert verdict["similarity"] == 0.4
        assert "verification_details" not in verdict

        # The calibrators stay inert on an empty snippet rather than
        # inventing agreement.
        assert _check_numeric_alignment(claim, {"content": ""}) == 0.0
        rel = _verify_fact_relationship(claim, {"content": ""})
        assert rel["reason"] == "no_source_text"
        assert rel["confidence_adjustment"] == 0.0
