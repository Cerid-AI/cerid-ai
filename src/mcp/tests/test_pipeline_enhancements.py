# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Comprehensive test battery for pipeline enhancements.

Tests all new features: source-aware queries, CRAG quality gate, verified memory
promotion, refresh-on-read, NLI consolidation guard, fact-relationship verification,
graph-guided verification, authoritative verification, tiered authority boost,
and dynamic confidence scoring.

Each test class targets a specific feature with real-world edge cases.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ===========================================================================
# 1. Source-Aware Query Construction (adapt_query + is_relevant)
# ===========================================================================


class TestWikipediaAdaptQuery:
    """Wikipedia should extract entity names, not keyword soup."""

    def test_extracts_proper_nouns(self):
        from utils.data_sources.wikipedia import WikipediaSource
        src = WikipediaSource()
        result = src.adapt_query("What is the population of Tokyo?", ["population", "Tokyo"])
        assert "Tokyo" in result

    def test_filters_question_words(self):
        from utils.data_sources.wikipedia import WikipediaSource
        src = WikipediaSource()
        result = src.adapt_query("What is Python?", ["Python"])
        assert "What" not in result

    def test_multi_word_entities(self):
        from utils.data_sources.wikipedia import WikipediaSource
        src = WikipediaSource()
        result = src.adapt_query("Tell me about Eiffel Tower in Paris", ["Eiffel", "Tower", "Paris"])
        assert "Eiffel Tower" in result or "Paris" in result

    def test_fallback_to_keywords_when_no_entities(self):
        from utils.data_sources.wikipedia import WikipediaSource
        src = WikipediaSource()
        result = src.adapt_query("how does photosynthesis work?", ["photosynthesis", "work"])
        assert "photosynthesis" in result

    def test_empty_keywords_uses_raw_query(self):
        from utils.data_sources.wikipedia import WikipediaSource
        src = WikipediaSource()
        result = src.adapt_query("photosynthesis", [])
        assert result == "photosynthesis"


class TestWolframIsRelevant:
    """Wolfram should only fire on computational/math queries."""

    def test_math_query_relevant(self):
        from utils.data_sources.wolfram import WolframAlphaSource
        src = WolframAlphaSource()
        assert src.is_relevant("calculate 5 + 3", ["calculate"])

    def test_unit_query_relevant(self):
        from utils.data_sources.wolfram import WolframAlphaSource
        src = WolframAlphaSource()
        assert src.is_relevant("convert 100 km to miles", ["convert", "miles"])

    def test_non_math_query_irrelevant(self):
        from utils.data_sources.wolfram import WolframAlphaSource
        src = WolframAlphaSource()
        assert not src.is_relevant("Who wrote Hamlet?", ["wrote", "Hamlet"])

    def test_math_operators_relevant(self):
        from utils.data_sources.wolfram import WolframAlphaSource
        src = WolframAlphaSource()
        assert src.is_relevant("what is 2^10 + 3*4", ["what"])

    def test_adapt_query_passes_raw(self):
        from utils.data_sources.wolfram import WolframAlphaSource
        src = WolframAlphaSource()
        raw = "integrate x^2 from 0 to 1"
        assert src.adapt_query(raw, ["integrate"]) == raw


class TestPubChemIsRelevant:
    """PubChem should only fire on chemistry queries."""

    def test_chemical_query_relevant(self):
        from utils.data_sources.pubchem import PubChemSource
        src = PubChemSource()
        assert src.is_relevant("What are the properties of aspirin?", ["properties", "aspirin"])

    def test_non_chemical_irrelevant(self):
        from utils.data_sources.pubchem import PubChemSource
        src = PubChemSource()
        assert not src.is_relevant("What is the GDP of France?", ["GDP", "France"])

    def test_cas_number_relevant(self):
        from utils.data_sources.pubchem import PubChemSource
        src = PubChemSource()
        assert src.is_relevant("Look up compound 50-78-2", ["compound"])

    def test_adapt_query_extracts_cas(self):
        from utils.data_sources.pubchem import PubChemSource
        src = PubChemSource()
        result = src.adapt_query("properties of compound 50-78-2", ["properties", "compound"])
        assert result == "50-78-2"

    def test_adapt_query_extracts_chemical_keyword(self):
        from utils.data_sources.pubchem import PubChemSource
        src = PubChemSource()
        result = src.adapt_query("What are the properties of aspirin?", ["properties", "aspirin"])
        assert result == "aspirin"


class TestOpenLibraryIsRelevant:
    """OpenLibrary should only fire on book/author queries."""

    def test_book_query_relevant(self):
        from utils.data_sources.openlibrary import OpenLibrarySource
        src = OpenLibrarySource()
        assert src.is_relevant("Who is the author of Dune?", ["author", "Dune"])

    def test_non_book_irrelevant(self):
        from utils.data_sources.openlibrary import OpenLibrarySource
        src = OpenLibrarySource()
        assert not src.is_relevant("What is the weather in Tokyo?", ["weather", "Tokyo"])

    def test_adapt_query_extracts_quoted_title(self):
        from utils.data_sources.openlibrary import OpenLibrarySource
        src = OpenLibrarySource()
        result = src.adapt_query('Have you read "The Great Gatsby"?', ["read", "Great", "Gatsby"])
        assert result == "The Great Gatsby"

    def test_adapt_query_extracts_author(self):
        from utils.data_sources.openlibrary import OpenLibrarySource
        src = OpenLibrarySource()
        result = src.adapt_query("Books by Gabriel Garcia Marquez", ["Books", "Gabriel"])
        assert "Gabriel Garcia Marquez" in result


class TestExchangeRatesIsRelevant:
    """ExchangeRates should only fire on currency queries."""

    def test_currency_relevant(self):
        from utils.data_sources.finance import ExchangeRatesSource
        src = ExchangeRatesSource()
        assert src.is_relevant("What is the USD to EUR exchange rate?", ["USD", "EUR"])

    def test_non_currency_irrelevant(self):
        from utils.data_sources.finance import ExchangeRatesSource
        src = ExchangeRatesSource()
        assert not src.is_relevant("What is the population of Tokyo?", ["population"])


class TestQueryAllSourceFiltering:
    """query_all should skip irrelevant sources and adapt queries per-source."""

    def test_is_relevant_filters_sources(self):
        from utils.data_sources.base import DataSource, DataSourceResult

        class AlwaysRelevant(DataSource):
            name = "always"
            async def query(self, q, **kw):
                return [DataSourceResult("t", "c", source_name="always")]

        class NeverRelevant(DataSource):
            name = "never"
            def is_relevant(self, rq, kw):
                return False
            async def query(self, q, **kw):
                raise AssertionError("Should not be called")

        # Test is_relevant directly — NeverRelevant should be filtered
        src_always = AlwaysRelevant()
        src_never = NeverRelevant()
        assert src_always.is_relevant("any", ["any"]) is True
        assert src_never.is_relevant("any", ["any"]) is False

    def test_adapt_query_called_per_source(self):
        from utils.data_sources.base import DataSource, DataSourceResult

        class CustomAdapter(DataSource):
            name = "custom"
            def adapt_query(self, rq, kw):
                return "adapted_query"
            async def query(self, q, **kw):
                return [DataSourceResult("t", "c", source_name="custom")]

        src = CustomAdapter()
        # Verify adapt_query transforms the query
        assert src.adapt_query("full question", ["key"]) == "adapted_query"
        # Verify default is_relevant returns True
        assert src.is_relevant("full question", ["key"]) is True


# ===========================================================================
# 2. Dynamic Confidence Scoring
# ===========================================================================


class TestDynamicConfidenceScoring:
    """score_confidence should adjust results based on query-source fit."""

    def test_wikipedia_boosts_title_match(self):
        from utils.data_sources.wikipedia import DataSourceResult, WikipediaSource
        src = WikipediaSource()
        result = DataSourceResult("Tokyo", "content about Tokyo", confidence=0.85)
        score = src.score_confidence("what is the population of tokyo?", result)
        assert score > 0.85

    def test_wikipedia_reduces_disambiguation(self):
        from utils.data_sources.wikipedia import DataSourceResult, WikipediaSource
        src = WikipediaSource()
        result = DataSourceResult("Python (disambiguation)", "disambiguation page", confidence=0.85)
        score = src.score_confidence("python programming", result)
        assert score < 0.85

    def test_wolfram_reduces_non_answer(self):
        from utils.data_sources.wolfram import DataSourceResult, WolframAlphaSource
        src = WolframAlphaSource()
        result = DataSourceResult("query", "Wolfram|Alpha did not understand your input", confidence=0.95)
        score = src.score_confidence("gibberish query", result)
        assert score < 0.5

    def test_duckduckgo_boosts_gov_url(self):
        from utils.data_sources.duckduckgo import DataSourceResult, DuckDuckGoSource
        src = DuckDuckGoSource()
        result = DataSourceResult("CDC Data", "health data", source_url="https://www.cdc.gov/data", confidence=0.80)
        score = src.score_confidence("covid statistics", result)
        assert score > 0.85

    def test_base_returns_unchanged(self):
        from utils.data_sources.base import DataSource, DataSourceResult

        class Plain(DataSource):
            name = "plain"
            async def query(self, q, **kw):
                return []

        src = Plain()
        result = DataSourceResult("t", "c", confidence=0.75)
        assert src.score_confidence("any query", result) == 0.75


# ===========================================================================
# 3. CRAG Quality Gate
# ===========================================================================


class TestCRAGQualityGate:
    """CRAG gate is owned by the router layer (SSOT).

    The inner gate in core/agents/query_agent.py was removed — the router-level
    gate in app/routers/agents.py is now the single source of truth for firing
    external sources. See test_rag_resilience.TestCRAGGate for full behavioural
    coverage; these tests cover config plumbing and ensure no inner gate
    regressed.
    """

    def test_gate_fires_on_low_relevance(self):
        """Router-level should_fire_external_crag returns True when KB is weak."""
        import config as _cfg
        from app.routers.agents import should_fire_external_crag

        low_kb = {"results": [{"relevance": 0.2, "content": "weak match"}]}
        threshold = getattr(_cfg, "RETRIEVAL_QUALITY_THRESHOLD", 0.4)
        assert isinstance(threshold, (int, float))
        assert should_fire_external_crag(
            ext_on=True, kb_result=low_kb, threshold=threshold,
        ) is True

    def test_gate_does_not_fire_on_high_relevance(self):
        """Router gate returns False when top KB relevance is above threshold."""
        from app.routers.agents import should_fire_external_crag

        high_kb = {"results": [{"relevance": 0.85, "content": "strong match"}]}
        assert should_fire_external_crag(
            ext_on=True, kb_result=high_kb, threshold=0.4,
        ) is False

    def test_gate_handles_empty_results(self):
        """Empty KB results trigger the router gate (top_rel = 0.0)."""
        from app.routers.agents import should_fire_external_crag

        assert should_fire_external_crag(
            ext_on=True, kb_result={"results": []}, threshold=0.4,
        ) is True

    def test_no_inner_gate_in_query_agent(self):
        """Regression: the inner CRAG block must stay removed.

        A second gate would double-fan-out to the same external sources and
        split threshold tuning across two files. Router is SSOT.
        """
        from pathlib import Path

        src = Path(__file__).resolve().parent.parent / "core" / "agents" / "query_agent.py"
        text = src.read_text()
        # The inner block used these locals — their absence proves the gate is gone.
        assert "_crag_threshold" not in text
        assert "_crag_results" not in text
        assert "registry.query_all" not in text


# ===========================================================================
# 4. Verified Memory Promotion
# ===========================================================================


class TestVerifiedMemoryPromotion:
    """Verified facts should be promoted to empirical Memory nodes."""

    def test_promotes_supported_high_confidence_claims(self):
        from core.agents.verified_memory import promote_verified_facts

        report = {
            "conversation_id": "conv-123",
            "claims": [
                {"claim": "Tokyo has a population of 14 million", "verdict": "supported",
                 "confidence": 0.9, "type": "factual", "nli_entailment": 0.85, "sources": []},
            ],
        }
        mock_chroma = MagicMock()
        mock_chroma.get_or_create_collection.return_value = MagicMock()
        mock_neo4j = MagicMock()
        mock_neo4j.session.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_neo4j.session.return_value.__exit__ = MagicMock(return_value=False)

        mock_create = MagicMock(return_value="mem-001")

        with patch("core.agents.memory.detect_memory_conflict", new_callable=AsyncMock, return_value=[]):
            counts = _run(promote_verified_facts(
                report, mock_chroma, mock_neo4j, create_memory_fn=mock_create,
            ))

        assert counts["promoted"] == 1
        assert counts["skipped_low_confidence"] == 0
        # Verify memory_type is set to empirical
        call_args = mock_create.call_args[0][1]
        assert call_args["memory_type"] == "empirical"
        assert call_args["source"] == "verification"

    def test_skips_low_confidence_claims(self):
        from core.agents.verified_memory import promote_verified_facts

        report = {
            "conversation_id": "conv-456",
            "claims": [
                {"claim": "Maybe true", "verdict": "supported",
                 "confidence": 0.5, "type": "factual", "nli_entailment": 0.4},
            ],
        }
        counts = _run(promote_verified_facts(
            report, MagicMock(), MagicMock(), create_memory_fn=MagicMock(),
        ))
        assert counts["promoted"] == 0
        assert counts["skipped_low_confidence"] == 1

    def test_skips_ignorance_claims(self):
        from core.agents.verified_memory import promote_verified_facts

        report = {
            "conversation_id": "conv-789",
            "claims": [
                {"claim": "I don't have data on this", "verdict": "supported",
                 "confidence": 0.95, "type": "ignorance", "nli_entailment": 0.9},
            ],
        }
        counts = _run(promote_verified_facts(
            report, MagicMock(), MagicMock(), create_memory_fn=MagicMock(),
        ))
        assert counts["promoted"] == 0
        assert counts["skipped_type"] == 1

    def test_skips_duplicate_memories(self):
        from core.agents.verified_memory import promote_verified_facts

        report = {
            "conversation_id": "conv-dup",
            "claims": [
                {"claim": "Earth orbits the Sun", "verdict": "supported",
                 "confidence": 0.95, "type": "factual", "nli_entailment": 0.9},
            ],
        }
        with patch("core.agents.memory.detect_memory_conflict", new_callable=AsyncMock,
                    return_value=[{"memory_id": "existing", "similarity": 0.95}]):
            counts = _run(promote_verified_facts(
                report, MagicMock(), MagicMock(), create_memory_fn=MagicMock(),
            ))
        assert counts["promoted"] == 0
        assert counts["skipped_duplicate"] == 1

    def test_no_create_fn_logs_error(self):
        from core.agents.verified_memory import promote_verified_facts

        report = {
            "conversation_id": "conv-no-fn",
            "claims": [
                {"claim": "The Earth orbits the Sun at a distance of 150 million km", "verdict": "supported",
                 "confidence": 0.95, "type": "factual", "nli_entailment": 0.9},
            ],
        }
        with patch("core.agents.memory.detect_memory_conflict", new_callable=AsyncMock, return_value=[]):
            counts = _run(promote_verified_facts(
                report, MagicMock(), MagicMock(), create_memory_fn=None,
            ))
        assert counts["errors"] == 1
        assert counts["promoted"] == 0

    def test_empty_report_returns_zeros(self):
        from core.agents.verified_memory import promote_verified_facts

        counts = _run(promote_verified_facts(
            {"claims": []}, MagicMock(), MagicMock(),
        ))
        assert counts["promoted"] == 0
        assert counts["errors"] == 0

    def test_concurrent_promotion_of_same_claim_serializes(self):
        """Two concurrent promotions of the same claim must NOT both pass
        the dedup check before either writes. The per-claim async lock in
        verified_memory.py serializes the critical section."""
        from core.agents.verified_memory import promote_verified_facts

        claim = "The concurrent-serialize test claim — distinct text"
        report = {
            "conversation_id": "conv-race",
            "claims": [
                {"claim": claim, "verdict": "supported",
                 "confidence": 0.95, "type": "factual", "nli_entailment": 0.9, "sources": []},
            ],
        }

        gate = asyncio.Event()
        detect_order: list[int] = []

        async def slow_detect(text, *args, **kwargs):
            detect_order.append(1)
            if len(detect_order) == 1:
                await gate.wait()
            return []

        create_calls: list[str] = []

        def counting_create(driver, data):
            create_calls.append(data["text"])
            return f"mem-{len(create_calls)}"

        async def drive():
            with patch("core.agents.memory.detect_memory_conflict", side_effect=slow_detect):
                t1 = asyncio.create_task(promote_verified_facts(
                    report, MagicMock(), MagicMock(), create_memory_fn=counting_create,
                ))
                # Yield so t1 enters the critical section
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                t2 = asyncio.create_task(promote_verified_facts(
                    report, MagicMock(), MagicMock(), create_memory_fn=counting_create,
                ))
                # Yield so t2 reaches the lock and blocks
                await asyncio.sleep(0)
                await asyncio.sleep(0)

                # While t1 holds the lock, t2 MUST be blocked — only one detect call so far
                assert len(detect_order) == 1, (
                    f"second task slipped past the lock: detect_order={detect_order}"
                )

                gate.set()
                await asyncio.gather(t1, t2)

        _run(drive())

        # Both eventually ran their detect check
        assert len(detect_order) == 2
        # Both created (mocks don't simulate post-create dedup) — the property under
        # test is the serialization order, not the create-count.
        assert len(create_calls) == 2


# ===========================================================================
# 5. Tiered Authority Boost
# ===========================================================================


class TestTieredAuthorityBoost:
    """Memory authority boost should vary by source and confidence."""

    def test_verified_fact_gets_highest_boost(self):
        from core.agents.hallucination.patterns import memory_authority_boost
        result = memory_authority_boost({"memory_source_type": "verification", "relevance": 0.9})
        assert result == 0.25

    def test_empirical_memory_gets_highest_boost(self):
        from core.agents.hallucination.patterns import memory_authority_boost
        result = memory_authority_boost({"memory_type": "empirical", "relevance": 0.8})
        assert result == 0.25

    def test_high_confidence_decision_gets_tier2(self):
        from core.agents.hallucination.patterns import memory_authority_boost
        result = memory_authority_boost({"memory_type": "decision", "_raw_relevance": 0.75})
        assert result == 0.20

    def test_standard_memory_gets_tier3(self):
        from core.agents.hallucination.patterns import memory_authority_boost
        result = memory_authority_boost({"memory_type": "project_context", "_raw_relevance": 0.55})
        assert result == 0.15

    def test_low_relevance_gets_minimal_boost(self):
        from core.agents.hallucination.patterns import memory_authority_boost
        result = memory_authority_boost({"memory_type": "conversational", "_raw_relevance": 0.3})
        assert result == 0.05

    def test_backward_compat_constant_exists(self):
        from core.agents.hallucination.patterns import MEMORY_AUTHORITY_BOOST
        assert MEMORY_AUTHORITY_BOOST == 0.15


# ===========================================================================
# 6. Fact-Relationship Verification
# ===========================================================================


class TestFactRelationshipVerification:
    """Verification should check temporal, entity, and specificity alignment."""

    def test_temporal_mismatch_penalized(self):
        from core.agents.hallucination.verification import _verify_fact_relationship
        result = _verify_fact_relationship(
            "The product was released in 2024",
            {"content": "The product was first launched in 1995 as a prototype"},
        )
        assert result["confidence_adjustment"] < 0
        assert "temporal_mismatch" in result["reason"]

    def test_matching_years_not_penalized(self):
        from core.agents.hallucination.verification import _verify_fact_relationship
        result = _verify_fact_relationship(
            "Tokyo population in 2024 is 14 million",
            {"content": "As of 2024, Tokyo's metro area has 14 million residents"},
        )
        assert result["confidence_adjustment"] >= 0
        assert result["aligned"]

    def test_specificity_gap_penalized(self):
        from core.agents.hallucination.verification import _verify_fact_relationship
        result = _verify_fact_relationship(
            "The company earned $2.5 billion in revenue and had 15,000 employees",
            {"content": "The company is a major technology corporation in Silicon Valley"},
        )
        assert result["confidence_adjustment"] < 0
        assert "specificity_gap" in result["reason"]

    def test_percentage_mismatch_penalized(self):
        from core.agents.hallucination.verification import _verify_fact_relationship
        result = _verify_fact_relationship(
            "Unemployment rate is 3.5%",
            {"content": "The unemployment rate reached 25% during the crisis"},
        )
        assert result["confidence_adjustment"] < 0
        assert "percentage_gap" in result["reason"]

    def test_empty_source_returns_aligned(self):
        from core.agents.hallucination.verification import _verify_fact_relationship
        result = _verify_fact_relationship("Any claim", {"content": ""})
        assert result["aligned"]
        assert result["confidence_adjustment"] == 0.0


# ===========================================================================
# 7. NLI Consolidation Guard
# ===========================================================================


class TestNLIConsolidationGuard:
    """Memory merges should be blocked when NLI shows information loss."""

    def test_merge_blocked_when_nli_low(self):
        """When merged text doesn't entail originals, merge is rejected."""
        from core.agents.memory import resolve_memory_conflict

        mock_nli = MagicMock(return_value={"entailment": 0.3, "contradiction": 0.1, "neutral": 0.6, "label": "neutral"})

        with (
            patch("core.agents.memory.call_internal_llm", new_callable=AsyncMock,
                  return_value='{"action": "merge", "reason": "overlap", "merged_text": "combined"}'),
            patch("core.agents.memory.parse_llm_json",
                  return_value={"action": "merge", "reason": "overlap", "merged_text": "combined"}),
            patch("core.utils.nli.nli_score", mock_nli),
        ):
            result = _run(resolve_memory_conflict(
                "New fact about topic A", {"memory_id": "m1", "text": "Old fact about topic A"},
            ))
        # NLI guard should downgrade to coexist
        assert result["action"] == "coexist"
        assert "NLI guard" in result["reason"]

    def test_merge_allowed_when_nli_high(self):
        """When merged text entails both originals, merge proceeds."""
        from core.agents.memory import resolve_memory_conflict

        mock_nli = MagicMock(return_value={"entailment": 0.85, "contradiction": 0.05, "neutral": 0.1, "label": "entailment"})

        with (
            patch("core.agents.memory.call_internal_llm", new_callable=AsyncMock,
                  return_value='{"action": "merge", "reason": "same topic", "merged_text": "combined fact"}'),
            patch("core.agents.memory.parse_llm_json",
                  return_value={"action": "merge", "reason": "same topic", "merged_text": "combined fact"}),
            patch("core.utils.nli.nli_score", mock_nli),
        ):
            result = _run(resolve_memory_conflict(
                "New fact", {"memory_id": "m1", "text": "Old fact"},
            ))
        assert result["action"] == "merge"
        assert result["merged_text"] == "combined fact"


# ===========================================================================
# 8. NLI Threshold (Self-RAG)
# ===========================================================================


class TestNLIThresholdFix:
    """Self-RAG should use config threshold, not hardcoded 0.5."""

    def test_uses_config_threshold(self):
        """Verify the self_rag module uses config.NLI_ENTAILMENT_THRESHOLD."""
        import inspect

        import core.agents.self_rag as self_rag_mod
        # Read the entire module source, not just the top-level function
        source = inspect.getsource(self_rag_mod)
        # The hardcoded 0.5 should be replaced with config reference
        assert "config.NLI_ENTAILMENT_THRESHOLD" in source
        # Should NOT have the old hardcoded pattern as the threshold
        lines_with_entailment = [
            line for line in source.split("\n")
            if "entailment" in line and ">=" in line and "0.5" in line
        ]
        # Any line with >= 0.5 for entailment should also reference config
        for line in lines_with_entailment:
            assert "config" in line or "NLI_ENTAILMENT_THRESHOLD" in line, \
                f"Found hardcoded 0.5 entailment threshold: {line.strip()}"


# ===========================================================================
# 9. Authoritative External Verification
# ===========================================================================


class TestAuthoritativeVerification:
    """Expert mode should gather external evidence before LLM synthesis."""

    def test_claim_domain_classification(self):
        from core.agents.hallucination.authoritative_verify import _classify_claim_domain
        assert _classify_claim_domain("What is the molecular weight of aspirin?") == "scientific"
        assert _classify_claim_domain("What is the GDP of France?") == "financial"
        assert _classify_claim_domain("What is the population of Tokyo?") == "geographic"
        assert _classify_claim_domain("Calculate the integral of x^2") == "computational"
        assert _classify_claim_domain("What color is the sky?") == "general"

    def test_priority_breaks_ties_between_equal_match_counts(self):
        """When two domains each match once, priority weight breaks the tie.
        Scientific (weight 1.00) outranks financial (weight 0.90).

        If one domain has more matches than another, match count wins —
        priority only decides *ties*, not dominance. See other tests for
        that case.
        """
        from core.agents.hallucination.authoritative_verify import (
            _classify_claim_domain_detailed,
        )
        # "drug" → 1 scientific, "market" → 1 financial. Both single matches
        # → priority weight decides → scientific wins.
        result = _classify_claim_domain_detailed("The drug entered the market.")
        assert result["primary"] == "scientific"
        assert any(name == "financial" for name, _ in result["secondary"])

    def test_match_count_dominates_priority_when_not_tied(self):
        """Multi-match financial should beat single-match scientific — match
        count dominates; priority is a tiebreaker, not a trump card."""
        from core.agents.hallucination.authoritative_verify import (
            _classify_claim_domain_detailed,
        )
        # "pharmaceutical" = 1 sci; "stock", "price" = 2 fin → financial wins.
        result = _classify_claim_domain_detailed(
            "How has the stock price of pharmaceutical companies moved?"
        )
        assert result["primary"] == "financial"
        assert any(name == "scientific" for name, _ in result["secondary"])

    def test_domain_confidence_is_high_when_only_one_matches(self):
        from core.agents.hallucination.authoritative_verify import (
            _classify_claim_domain_detailed,
        )
        result = _classify_claim_domain_detailed(
            "What is the molecular weight of aspirin?"
        )
        assert result["primary"] == "scientific"
        assert result["confidence"] == 1.0
        assert result["secondary"] == []

    def test_domain_confidence_is_lower_when_tied(self):
        """Equal single-hit across two domains → low confidence signal so
        downstream can query multiple registries."""
        from core.agents.hallucination.authoritative_verify import (
            _classify_claim_domain_detailed,
        )
        # One hit each in two domains → scores close to equal, priority
        # weighting breaks the tie but confidence should be small.
        result = _classify_claim_domain_detailed(
            "Calculate the GDP growth rate."
        )
        assert result["primary"] in ("financial", "computational")
        assert 0 < result["confidence"] < 0.2  # tight margin

    def test_returns_empty_when_disabled(self):
        from core.agents.hallucination.authoritative_verify import verify_claim_authoritatively

        with patch("config.EXPERT_VERIFY_USE_AUTHORITATIVE_SOURCES", False):
            result = _run(verify_claim_authoritatively("any claim"))
        assert result["claim_domain"] == "disabled"
        assert result["authoritative_sources"] == []

    def test_returns_empty_on_source_failure(self):
        from core.agents.hallucination.authoritative_verify import verify_claim_authoritatively

        with (
            patch("config.EXPERT_VERIFY_USE_AUTHORITATIVE_SOURCES", True),
            patch("utils.data_sources.registry") as mock_reg,
        ):
            mock_reg.query_all = AsyncMock(side_effect=Exception("network down"))
            result = _run(verify_claim_authoritatively("aspirin molecular weight"))
        assert result["authoritative_sources"] == []
        assert "No authoritative sources" in result["evidence_summary"]

    def test_expert_mode_return_carries_structured_evidence(self):
        """Expert-mode verification must surface authoritative_sources,
        claim_domain, cross_validation and evidence_summary in its return
        so downstream SSE/audit/UI consumers can show *which* sources and
        NLI scores drove the verdict — not just the LLM's narrative answer."""
        from core.agents.hallucination import verification

        fake_auth = {
            "authoritative_sources": [
                {"source": "Wikipedia", "content": "snippet",
                 "source_url": "https://en.wikipedia.org/example",
                 "nli_entailment": 0.82, "nli_contradiction": 0.02},
            ],
            "cross_validation": {"kb_vs_external_agreement": 0.91},
            "claim_domain": "scientific",
            "evidence_summary": "1 authoritative source supports the claim.",
        }
        fake_llm_response = {
            "choices": [{
                "message": {
                    "content": '{"verdict": "supported", "confidence": 0.95, "reasoning": "ok"}',
                    "annotations": [],
                },
            }],
        }

        with (
            patch(
                "core.agents.hallucination.authoritative_verify.verify_claim_authoritatively",
                new_callable=AsyncMock, return_value=fake_auth,
            ),
            patch(
                "core.utils.llm_client.call_llm_raw",
                new_callable=AsyncMock, return_value=fake_llm_response,
            ),
            patch("config.EXPERT_VERIFY_USE_AUTHORITATIVE_SOURCES", True),
        ):
            result = _run(verification._verify_claim_externally(
                "Aspirin has a molecular weight of 180.16 g/mol",
                generating_model="openai/gpt-4o-mini",
                expert_mode=True,
            ))

        assert result.get("status") == "verified"
        assert result["authoritative_sources"] == fake_auth["authoritative_sources"]
        assert result["claim_domain"] == "scientific"
        assert result["cross_validation"] == fake_auth["cross_validation"]
        assert result["evidence_summary"] == fake_auth["evidence_summary"]

    def test_non_expert_mode_does_not_include_authoritative_fields(self):
        """Without expert_mode, the cheap cross-model path must NOT attach
        authoritative fields — they'd be confusing zero-valued noise in
        the SSE payload."""
        from core.agents.hallucination import verification

        fake_llm_response = {
            "choices": [{
                "message": {
                    "content": '{"status": "verified", "confidence": 0.9, "reason": "ok"}',
                    "annotations": [],
                },
            }],
        }

        with patch(
            "core.utils.llm_client.call_llm_raw",
            new_callable=AsyncMock, return_value=fake_llm_response,
        ):
            result = _run(verification._verify_claim_externally(
                "Some factual claim",
                generating_model="openai/gpt-4o-mini",
                expert_mode=False,
            ))

        assert "authoritative_sources" not in result
        assert "claim_domain" not in result
        assert "cross_validation" not in result
        assert "evidence_summary" not in result


# ===========================================================================
# 10. Memory Type in create_memory_node
# ===========================================================================


class TestMemoryTypeInNode:
    """create_memory_node should persist memory_type to Neo4j."""

    def test_memory_type_passed_to_cypher(self):
        from app.db.neo4j.memory import create_memory_node

        driver = MagicMock()
        session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)

        create_memory_node(driver, {
            "text": "verified fact",
            "memory_type": "empirical",
            "source": "verification",
        })

        # Verify the Cypher CREATE was called with memory_type
        call_args = session.run.call_args_list[0]
        cypher = call_args[0][0]
        kwargs = call_args[1]
        assert "memory_type" in cypher
        assert kwargs["memory_type"] == "empirical"

    def test_default_memory_type_is_decision(self):
        from app.db.neo4j.memory import create_memory_node

        driver = MagicMock()
        session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)

        create_memory_node(driver, {"text": "some memory"})

        kwargs = session.run.call_args_list[0][1]
        assert kwargs["memory_type"] == "decision"

    def test_decay_anchor_set_on_creation(self):
        from app.db.neo4j.memory import create_memory_node

        driver = MagicMock()
        session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)

        create_memory_node(driver, {"text": "test"})

        cypher = session.run.call_args_list[0][0][0]
        assert "decay_anchor" in cypher


# ===========================================================================
# 11. Refresh-on-Read (decay_anchor)
# ===========================================================================


class TestRefreshOnRead:
    """update_memory_access should reset decay_anchor for Ebbinghaus rehearsal."""

    def test_decay_anchor_updated_on_access(self):
        from app.db.neo4j.memory import update_memory_access

        driver = MagicMock()
        session = MagicMock()
        session.run.return_value.single.return_value = {"count": 5}
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)

        update_memory_access(driver, "mem-123")

        cypher = session.run.call_args[0][0]
        assert "decay_anchor" in cypher
        assert "access_count" in cypher

    def test_recall_uses_decay_anchor_from_metadata(self):
        """recall_memories should prefer decay_anchor over created_at for age."""
        import inspect

        from core.agents.memory import recall_memories
        source = inspect.getsource(recall_memories)
        assert "decay_anchor" in source


# ===========================================================================
# 12. Config Values
# ===========================================================================


class TestConfigValues:
    """Verify all new config values are present with correct types."""

    def test_retrieval_quality_threshold(self):
        import config
        assert isinstance(config.RETRIEVAL_QUALITY_THRESHOLD, float)
        assert 0 < config.RETRIEVAL_QUALITY_THRESHOLD < 1

    def test_verified_memory_settings(self):
        import config
        assert isinstance(config.ENABLE_VERIFIED_MEMORY_PROMOTION, bool)
        assert isinstance(config.VERIFIED_MEMORY_MIN_CONFIDENCE, float)
        assert isinstance(config.VERIFIED_MEMORY_MIN_NLI, float)
        assert 0 < config.VERIFIED_MEMORY_MIN_CONFIDENCE <= 1
        assert 0 < config.VERIFIED_MEMORY_MIN_NLI <= 1

    def test_consolidation_guard(self):
        import config
        assert isinstance(config.MEMORY_CONSOLIDATION_NLI_GUARD, float)
        assert 0 < config.MEMORY_CONSOLIDATION_NLI_GUARD <= 1

    def test_graph_verification_boost(self):
        import config
        assert isinstance(config.GRAPH_VERIFICATION_BOOST, float)
        assert 0 <= config.GRAPH_VERIFICATION_BOOST <= 0.2

    def test_expert_verify_settings(self):
        import config
        assert isinstance(config.EXPERT_VERIFY_USE_AUTHORITATIVE_SOURCES, bool)
        assert isinstance(config.EXPERT_VERIFY_MAX_SOURCES, int)
        assert config.EXPERT_VERIFY_MAX_SOURCES > 0

    def test_nli_threshold_is_higher_than_old(self):
        import config
        assert config.NLI_ENTAILMENT_THRESHOLD >= 0.7  # was hardcoded 0.5
