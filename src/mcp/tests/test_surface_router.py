# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Unit tests for the surface router (Phase K3.1) + pkb_surface_route tool."""
from __future__ import annotations

import pytest

from core.retrieval.surface_router import classify_intent, route


class TestRoutingFindingsFixed:
    """Regression tests for the routing findings surfaced by the eval harness."""

    @pytest.mark.parametrize("query", [
        "what is my preference for budgeting",
        "what are our preferences here",
        "remind me of our decision on the schema",
        "summarize my opinion on remote work",
    ])
    def test_finding_2_preference_questions_route_to_memory(self, query):
        # Finding #2: the compiled_summary "what is X" pattern used to swallow
        # preference/decision questions, so the memory surface (A2) never fired.
        intent, _, _ = classify_intent(query)
        assert intent == "personal_context", f"{query!r} → {intent}"

    @pytest.mark.parametrize("query", [
        "B-tree versus GIN indexes in Postgres",
        "Optional vs Union in Python type hints",
        "Redis vs. Memcached",
        "compare Roth versus Traditional",
    ])
    def test_finding_3_vs_versus_routes_relational(self, query):
        # Finding #3: "vs"/"vs."/"versus" comparisons should consult the graph
        # surface, not fall through to mixed.
        intent, _, _ = classify_intent(query)
        assert intent == "relational", f"{query!r} → {intent}"

    def test_plain_what_is_still_compiled_summary(self):
        # The fixes must not over-capture: a plain definitional query is unchanged.
        assert classify_intent("what is photosynthesis")[0] == "compiled_summary"
        assert classify_intent("what is my budget")[0] == "compiled_summary"  # 'budget' not a personal noun

# ---------------------------------------------------------------------------
# Intent classification (regex pass)
# ---------------------------------------------------------------------------


class TestClassifyIntent:
    @pytest.mark.parametrize("query", [
        "What is Tesla?",
        "Who is Elon Musk",
        "tell me about CRISPR",
        "Give me an overview of zero-knowledge proofs",
        "Summarize the Transformer architecture",
    ])
    def test_compiled_summary_recognised(self, query):
        intent, conf, _ = classify_intent(query)
        assert intent == "compiled_summary"
        assert conf == 1.0

    @pytest.mark.parametrize("query", [
        'Find the email where Elon said "model 3 production"',
        'Where does the source say "quarterly revenue"',
        "Find the passage where Tesla announces a new model",
        "Show me the sentence where my doctor mentioned the dosage",
    ])
    def test_specific_fact_recognised(self, query):
        intent, conf, _ = classify_intent(query)
        assert intent == "specific_fact"
        assert conf == 1.0

    @pytest.mark.parametrize("query", [
        "How does Tesla relate to SpaceX?",
        "What connects Elon Musk and Twitter",
        "Compare Tesla and Ford on EV strategy",
        "list everything mentioning the new factory",
        "what entities are connected to OpenAI",
    ])
    def test_relational_recognised(self, query):
        intent, conf, _ = classify_intent(query)
        assert intent == "relational"
        assert conf == 1.0

    @pytest.mark.parametrize("query", [
        "What did we decide about the migration?",
        "Did I mention the dosage change?",
        "Last time we discussed Tesla earnings",
        "my preference for the coffee machine",
        "I prefer slack to email",
    ])
    def test_personal_context_recognised(self, query):
        intent, conf, _ = classify_intent(query)
        assert intent == "personal_context"
        assert conf == 1.0

    @pytest.mark.parametrize("query", [
        "Random sentence with no clear intent here",
        "The latest news",
        "Show progress",
    ])
    def test_mixed_fallback(self, query):
        intent, conf, _ = classify_intent(query)
        assert intent == "mixed"
        assert conf == 0.5

    def test_empty_query_returns_mixed(self):
        intent, _, _ = classify_intent("")
        assert intent == "mixed"


# ---------------------------------------------------------------------------
# Surface mapping
# ---------------------------------------------------------------------------


class TestRouteSurfaces:
    def test_compiled_summary_routes_to_wiki_first(self):
        r = route("What is Tesla?")
        assert r.primary == "wiki"
        assert r.surfaces[0] == "wiki"
        assert "vector" in r.surfaces

    def test_specific_fact_routes_to_vector_first(self):
        r = route('Find the email where Elon said "production"')
        assert r.primary == "vector"

    def test_relational_routes_to_graph_first(self):
        r = route("How does Tesla relate to SpaceX?")
        assert r.primary == "graph"

    def test_personal_context_routes_to_memory_first(self):
        r = route("What did we decide about the migration?")
        assert r.primary == "memory"

    def test_mixed_routes_to_vector_with_all_surfaces(self):
        r = route("Random sentence with no clear intent")
        assert r.primary == "vector"  # safe default for ambiguous
        assert set(r.surfaces) >= {"wiki", "vector", "graph"}

    def test_compiled_summary_extracts_entity_hint(self):
        r = route("What is Tesla?")
        assert r.matched_entity_hint is not None
        assert "tesla" in r.matched_entity_hint.lower()

    def test_relational_no_entity_hint(self):
        r = route("How does Tesla relate to SpaceX?")
        assert r.matched_entity_hint is None


# ---------------------------------------------------------------------------
# Personal-context wins over compiled-summary (precedence)
# ---------------------------------------------------------------------------


class TestPrecedence:
    def test_we_decided_about_X_is_personal_not_summary(self):
        """'What did we decide about X' must NOT route to wiki even though
        X looks like a summary target — personal context wins."""
        r = route("What did we decide about Tesla?")
        assert r.primary == "memory"
        assert r.intent == "personal_context"

    def test_quoted_span_wins_over_compiled_summary(self):
        r = route('Tell me about "the new factory"')
        assert r.intent == "specific_fact"


# ---------------------------------------------------------------------------
# pkb_surface_route MCP tool
# ---------------------------------------------------------------------------


class TestSurfaceRouteTool:
    @pytest.mark.asyncio
    async def test_returns_full_payload(self):
        from app.mcp_tools.router import pkb_surface_route

        result = await pkb_surface_route("What is Tesla?")
        assert result["primary"] == "wiki"
        assert "wiki" in result["surfaces"]
        assert result["intent"] == "compiled_summary"
        assert result["confidence"] == 1.0
        assert "tesla" in (result["matched_entity_hint"] or "").lower()

    @pytest.mark.asyncio
    async def test_empty_query_raises(self):
        from app.mcp_tools.router import pkb_surface_route
        from app.tool_registry import InvalidParamsError

        with pytest.raises(InvalidParamsError):
            await pkb_surface_route("   ")
