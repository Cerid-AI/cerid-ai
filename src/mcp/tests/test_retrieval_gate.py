# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for adaptive retrieval gate."""

import pytest

from utils.retrieval_gate import RetrievalDecision, classify_retrieval_need


class TestClassifyRetrievalNeed:
    """Tests for classify_retrieval_need()."""

    def test_empty_query_returns_skip(self):
        d = classify_retrieval_need("")
        assert d.action == "skip"
        assert d.reason == "empty_query"

    def test_whitespace_query_returns_skip(self):
        d = classify_retrieval_need("   ")
        assert d.action == "skip"
        assert d.reason == "empty_query"

    def test_greeting_returns_skip(self):
        for greeting in ["hi", "Hello", "hey there", "Hi!", "hello"]:
            d = classify_retrieval_need(greeting)
            assert d.action == "skip", f"Expected skip for {greeting!r}"

    def test_thanks_returns_skip(self):
        d = classify_retrieval_need("thanks")
        assert d.action == "skip"

    def test_acknowledgment_returns_skip(self):
        for ack in ["yes", "no", "sure", "ok", "nope"]:
            d = classify_retrieval_need(ack)
            assert d.action == "skip", f"Expected skip for {ack!r}"

    def test_compliment_returns_skip(self):
        for phrase in ["great", "awesome!", "cool", "nice"]:
            d = classify_retrieval_need(phrase)
            assert d.action == "skip", f"Expected skip for {phrase!r}"

    def test_rephrase_request_returns_skip(self):
        d = classify_retrieval_need("can you rephrase that?")
        assert d.action == "skip"

    def test_short_two_word_no_question_mark_returns_skip(self):
        d = classify_retrieval_need("got it")
        assert d.action == "skip"
        assert d.reason == "too_short"

    def test_simple_lookup_returns_light(self):
        d = classify_retrieval_need("what is kubernetes?")
        assert d.action == "light"
        assert d.reason == "simple_lookup"

    def test_define_returns_light(self):
        d = classify_retrieval_need("define polymorphism concept?")
        assert d.action == "light"

    def test_show_me_returns_light(self):
        d = classify_retrieval_need("show me the config")
        assert d.action == "light"

    def test_comparison_returns_full(self):
        d = classify_retrieval_need("compare React vs Vue for state management")
        assert d.action == "full"
        assert d.reason == "complex_query"

    def test_multi_question_returns_full(self):
        d = classify_retrieval_need("What is X? And how does Y work?")
        assert d.action == "full"

    def test_analytical_query_returns_full(self):
        d = classify_retrieval_need("analyze the performance of our ingestion pipeline")
        assert d.action == "full"

    def test_long_how_question_returns_full(self):
        d = classify_retrieval_need("how does the cross-encoder reranking pipeline work in detail")
        assert d.action == "full"

    def test_default_full_for_normal_query(self):
        d = classify_retrieval_need("what are the best practices for chunking?")
        assert d.action == "full"

    def test_top_k_on_light_decision(self):
        d = classify_retrieval_need("what is polymorphism?")
        assert d.action == "light"
        assert d.top_k > 0

    def test_top_k_zero_on_skip(self):
        d = classify_retrieval_need("hi")
        assert d.top_k == 0
