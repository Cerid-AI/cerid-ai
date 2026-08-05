# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for core.agents.query_router (Phase 4b.3)."""
from __future__ import annotations

import pytest

from core.agents.query_router import (
    has_proper_noun,
    has_quoted_span,
    route,
    word_count,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class TestWordCount:
    def test_simple(self):
        assert word_count("one two three") == 3

    def test_empty(self):
        assert word_count("") == 0
        assert word_count("   ") == 0


class TestHasQuotedSpan:
    @pytest.mark.parametrize("query", [
        '"WWDC 2024" keynote details',
        "What did 'Apple' announce",
        'phrase with "embedded quotes" inline',
    ])
    def test_detects_quotes(self, query):
        assert has_quoted_span(query)

    @pytest.mark.parametrize("query", [
        "no quotes here at all",
        "what's a contraction",  # apostrophe in contraction not a quoted span
        "just one \" stray quote",  # unbalanced — not a span
    ])
    def test_misses_non_spans(self, query):
        assert not has_quoted_span(query)


class TestHasProperNoun:
    @pytest.mark.parametrize("query", [
        "tell me about Apple",
        "what does the Federal Reserve think",
        "how does BTC compare to ETH",
        "research on MAPK signalling",
    ])
    def test_detects_capitals_not_at_start(self, query):
        assert has_proper_noun(query)

    @pytest.mark.parametrize("query", [
        "how does monetary policy affect markets",
        "what causes economic recessions",
        "explain the concept of inflation in simple terms",
        "How does monetary policy affect markets",  # leading capital is sentence start
    ])
    def test_pure_lowercase_or_leading_cap_only(self, query):
        assert not has_proper_noun(query)


# ---------------------------------------------------------------------------
# route — full decision matrix
# ---------------------------------------------------------------------------

class TestRoute:
    def test_short_query_routes_local(self):
        assert route("hello world") == "local_graphrag"

    def test_short_thematic_query_routes_local(self):
        # 5 words — short, but thematic. Local because length threshold dominates.
        assert route("how does inflation affect markets") == "local_graphrag"

    def test_long_thematic_query_routes_global(self):
        q = (
            "how does monetary policy interact with broad market sentiment "
            "across different economic conditions and time horizons"
        )
        assert word_count(q) > 15
        assert not has_quoted_span(q)
        assert not has_proper_noun(q)
        assert route(q) == "global_graphrag"

    def test_long_query_with_quotes_routes_local(self):
        q = (
            'tell me everything about "WWDC 2024" and what was announced '
            "during the keynote address regarding artificial intelligence"
        )
        assert route(q) == "local_graphrag"

    def test_long_query_with_proper_noun_routes_local(self):
        q = (
            "tell me about how Apple Inc strategy evolved over the last "
            "decade and what implications follow for product development"
        )
        assert route(q) == "local_graphrag"

    def test_starts_with_capital_alone_does_not_force_local(self):
        # "How" at position 0 is a sentence starter, not a proper noun.
        q = (
            "How does monetary policy interact with broad market sentiment "
            "across different economic conditions and various time horizons"
        )
        assert route(q) == "global_graphrag"
