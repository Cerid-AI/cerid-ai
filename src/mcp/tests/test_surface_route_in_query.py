# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""GA P0.5 A1a — the query path surfaces which knowledge surface a query routes to.

Until now ``surface_router`` was computed only by observability/MCP tooling and
never in the critical query path. A1a computes it once in ``_agent_query_impl``
and attaches ``surface_route`` to every return dict so callers/UI/eval can see
the chosen intent + surface (foundation for A1b behavioural biasing). This slice
is behaviour-neutral for ranking.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.agents.query_agent import (
    _agent_query_impl,
    _domains_no_results,
    _recall_memory_surface,
    _recall_wiki_surface,
    _should_skip_graph,
    _surface_route_dict,
    set_wiki_page_fetcher,
)

_RELATIONAL = {"intent": "relational", "primary": "graph"}
_FACT = {"intent": "specific_fact", "primary": "vector"}


class TestSurfaceRouteDict:
    @pytest.mark.parametrize(
        "query,intent,primary",
        [
            ("what is photosynthesis", "compiled_summary", "wiki"),
            ("how does inflation relate to interest rates", "relational", "graph"),
            ("what did we decide about the launch", "personal_context", "memory"),
            ('find the email that says "ship it"', "specific_fact", "vector"),
            ("asdf qwerty", "mixed", "vector"),
        ],
    )
    def test_intent_mapping(self, query, intent, primary):
        sr = _surface_route_dict(query)
        assert sr["intent"] == intent
        assert sr["primary"] == primary
        assert isinstance(sr["surfaces"], list) and sr["surfaces"]
        assert 0.0 <= sr["confidence"] <= 1.0

    def test_shape_is_json_serializable(self):
        import json

        sr = _surface_route_dict("tell me about MVCC")
        # Must round-trip — it rides in the API response (extra="allow").
        assert json.loads(json.dumps(sr)) == sr
        assert set(sr) == {"intent", "primary", "surfaces", "confidence", "matched_entity_hint"}


class TestSurfaceRouteInResult:
    async def test_consumer_restricted_path_carries_route(self):
        # allowed_domains filters every requested domain away → the early
        # consumer-restricted return fires before any retrieval; it must still
        # carry the computed surface route.
        result = await _agent_query_impl(
            "what is photosynthesis",
            domains=["coding"],
            allowed_domains=["nonexistent"],
            chroma_client=MagicMock(),
            redis_client=None,
        )
        assert result["retrieval_skipped"] is True
        assert result["surface_route"]["intent"] == "compiled_summary"
        assert result["surface_route"]["primary"] == "wiki"


class TestDomainsNoResults:
    """GA P0.5 B2b — report which requested domains returned nothing."""

    def test_flags_domains_with_zero_results(self):
        results = [{"domain": "coding", "relevance": 0.9}]
        assert _domains_no_results(["coding", "finance"], results) == ["finance"]

    def test_empty_results_flags_all_requested(self):
        assert _domains_no_results(["a", "b"], []) == ["a", "b"]

    def test_all_domains_hit_returns_empty(self):
        results = [{"domain": "a"}, {"domain": "b"}]
        assert _domains_no_results(["a", "b"], results) == []


class TestShouldSkipGraph:
    """GA P0.5 A1b — relational intent forces the graph surface when biased on.

    The flag is default-OFF, so default behaviour is unchanged (no eval-gated
    ranking change ships until the LongMemEval flip).
    """

    def test_default_off_uses_high_conf_early_exit(self):
        # relational, but biasing disabled → normal early-exit rule applies
        assert _should_skip_graph(10, 10, _RELATIONAL, biased_enabled=False) is True

    def test_relational_biased_never_skips(self):
        # relational + biased → always consult graph even with enough high-conf
        assert _should_skip_graph(10, 10, _RELATIONAL, biased_enabled=True) is False

    def test_non_relational_biased_uses_early_exit(self):
        assert _should_skip_graph(10, 10, _FACT, biased_enabled=True) is True

    def test_below_threshold_never_skips(self):
        # not enough high-confidence hits → graph runs regardless
        assert _should_skip_graph(3, 10, _FACT, biased_enabled=False) is False
        assert _should_skip_graph(3, 10, _FACT, biased_enabled=True) is False

    def test_at_threshold_skips_when_not_biased_relational(self):
        assert _should_skip_graph(10, 10, _FACT, biased_enabled=True) is True


class TestMemorySurface:
    """GA P0.5 A2 — recall episodic memories and adapt them into the result shape."""

    async def test_adapts_recall_memories_to_result_shape(self):
        from unittest.mock import AsyncMock, patch

        mems = [
            {"text": "we chose MVCC", "adjusted_score": 0.77, "memory_id": "mem-1", "memory_type": "decision"},
        ]
        with patch("core.agents.memory.recall_memories", new=AsyncMock(return_value=mems)):
            out = await _recall_memory_surface("what did we decide", MagicMock(), MagicMock(), 10)
        assert len(out) == 1
        r = out[0]
        assert r["content"] == "we chose MVCC"
        assert r["relevance"] == 0.77
        assert r["artifact_id"] == "mem-1"
        assert r["source_type"] == "memory"
        assert r["source_authority"] == "user_memory"
        assert r["domain"] == "conversations"
        assert r["memory_type"] == "decision"

    async def test_failure_degrades_to_empty(self):
        from unittest.mock import AsyncMock, patch

        with patch("core.agents.memory.recall_memories", new=AsyncMock(side_effect=RuntimeError("boom"))):
            out = await _recall_memory_surface("q", MagicMock(), MagicMock(), 10)
        assert out == []


class TestWikiSurface:
    """GA P0.5 C2 — compiled-wiki surface via the app-registered DI fetcher."""

    async def test_no_fetcher_is_noop(self):
        set_wiki_page_fetcher(None)
        assert await _recall_wiki_surface("photosynthesis") == []

    async def test_adapts_page_to_result_shape(self):
        from unittest.mock import AsyncMock

        set_wiki_page_fetcher(AsyncMock(return_value={"content": "X is a thing.", "title": "X", "slug": "x"}))
        try:
            out = await _recall_wiki_surface("X")
        finally:
            set_wiki_page_fetcher(None)
        assert len(out) == 1
        r = out[0]
        assert r["content"] == "X is a thing."
        assert r["source_type"] == "wiki"
        assert r["artifact_id"] == "x"
        assert r["domain"] == "wiki"
        assert r["relevance"] == 1.0

    async def test_empty_hint_is_noop(self):
        from unittest.mock import AsyncMock

        set_wiki_page_fetcher(AsyncMock(return_value={"content": "x"}))
        try:
            assert await _recall_wiki_surface("") == []
        finally:
            set_wiki_page_fetcher(None)

    async def test_fetcher_error_degrades(self):
        from unittest.mock import AsyncMock

        set_wiki_page_fetcher(AsyncMock(side_effect=RuntimeError("boom")))
        try:
            assert await _recall_wiki_surface("X") == []
        finally:
            set_wiki_page_fetcher(None)
