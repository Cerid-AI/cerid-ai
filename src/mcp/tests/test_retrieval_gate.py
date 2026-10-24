# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for adaptive retrieval gate."""

from core.retrieval.retrieval_gate import classify_retrieval_need


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

    def test_short_conversational_filler_returns_skip(self):
        for phrase in ["got it", "will do", "sounds good", "makes sense"]:
            d = classify_retrieval_need(phrase)
            assert d.action == "skip", f"Expected skip for {phrase!r}"
            assert d.reason == "conversational"

    def test_short_keyword_query_returns_light(self):
        # UX-07 / kb-idle-zero: "invoice receipt" against a populated mail
        # corpus returned kb:0 because the blanket <=2-word skip treated a
        # keyword lookup as conversational filler. Short content-bearing
        # queries must retrieve.
        for phrase in ["invoice receipt", "tax form", "quarterly report"]:
            d = classify_retrieval_need(phrase)
            assert d.action == "light", f"Expected light for {phrase!r}"
            assert d.reason == "short_keyword"
            assert d.top_k > 0

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

    def test_greeting_followed_by_content_retrieves(self):
        # Audit F37: the greeting pattern was anchored only at the start, so any
        # query opening with a greeting word skipped retrieval.
        for query in [
            "hello world program in rust",
            "thanks to the new index, what is our refund policy",
            "hey what does the onboarding doc say about laptops",
            "ok so where is the Q3 budget spreadsheet",
            "good night sleep tips from my parenting notes",
        ]:
            d = classify_retrieval_need(query)
            assert d.action != "skip", f"Expected retrieval for {query!r}"

    def test_greeting_with_filler_still_skips(self):
        for phrase in [
            "thank you", "thanks so much!", "hey, thanks", "ok thanks",
            "thanks for the help", "bye everyone", "hello again",
            "good morning", "Good evening!", "good morning team",
        ]:
            d = classify_retrieval_need(phrase)
            assert d.action == "skip", f"Expected skip for {phrase!r}"
            assert d.reason == "conversational"

    def test_rephrase_request_with_new_subject_retrieves(self):
        # Audit F64: "rephrase/summarize/explain this" was prefix-only, so a
        # request naming new content skipped retrieval.
        for query in [
            "please summarize this contract clause about termination",
            "can you explain this error in the auth module",
            "could you rewrite it using the style guide from the wiki",
        ]:
            d = classify_retrieval_need(query)
            assert d.action != "skip", f"Expected retrieval for {query!r}"

    def test_rephrase_request_about_prior_turn_still_skips(self):
        for phrase in [
            "can you rephrase that?", "please summarize the above",
            "could you explain it again", "can you rewrite this more simply",
        ]:
            d = classify_retrieval_need(phrase)
            assert d.action == "skip", f"Expected skip for {phrase!r}"

    def test_technical_term_matches_whole_words_only(self):
        # Audit F65: substring matching upgraded "format" (orm), "cheapest"
        # (heap) and "hashtag" (hash) to full retrieval.
        for query in ["what is the format?", "explain cheapest flights", "define a hashtag"]:
            d = classify_retrieval_need(query)
            assert d.action == "light", f"Expected light for {query!r}"
            assert d.reason == "simple_lookup"

    def test_technical_term_upgrade_still_fires(self):
        for query in ["what is a hash?", "define the hashes", "explain a state machine"]:
            d = classify_retrieval_need(query)
            assert d.action == "full", f"Expected full for {query!r}"
            assert d.reason == "technical_term_upgrade"
