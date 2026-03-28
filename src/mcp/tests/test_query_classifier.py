# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for query intent classifier."""

from __future__ import annotations

from utils.query_classifier import classify_query_intent, get_rag_config


class TestClassifyQueryIntent:
    def test_greeting_is_conversational(self):
        assert classify_query_intent("Hello!") == "conversational"

    def test_thanks_is_conversational(self):
        assert classify_query_intent("Thanks for that") == "conversational"

    def test_question_is_factual(self):
        assert classify_query_intent("What is the capital of France?") == "factual"

    def test_code_keywords(self):
        assert classify_query_intent("Debug this Python function") == "code"

    def test_creative_prompt(self):
        assert classify_query_intent("Write a poem about the ocean") == "creative"

    def test_analysis_prompt(self):
        assert classify_query_intent("Compare React and Vue") == "analytical"

    def test_default_is_factual(self):
        assert classify_query_intent("quantum mechanics") == "factual"


class TestGetRagConfig:
    def test_rag_config_factual_injects(self):
        config = get_rag_config("factual")
        assert config["inject"] is True

    def test_rag_config_conversational_skips(self):
        config = get_rag_config("conversational")
        assert config["top_k"] == 0
