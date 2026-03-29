# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for retrieval profile — per-chunk quality signals for adaptive search."""

from __future__ import annotations

from utils.retrieval_profile import (
    compute_retrieval_profile,
    deserialize_profile,
    get_hybrid_weights,
    get_rerank_weights,
    serialize_profile,
)

# ---------------------------------------------------------------------------
# Tests: compute_retrieval_profile
# ---------------------------------------------------------------------------


class TestComputeRetrievalProfile:
    def test_empty_text_returns_zero_profile(self):
        profile = compute_retrieval_profile("")
        assert profile["content_density"] == 0.0
        assert profile["keyword_richness"] == 0.0
        assert profile["table_ratio"] == 0.0
        assert profile["number_density"] == 0.0
        assert profile["preferred_strategy"] == "balanced"
        assert profile["score"] == 0.0

    def test_prose_document_vector_strategy(self):
        """Rich narrative prose should yield 'vector' strategy."""
        text = (
            "The discovery of penicillin revolutionized modern medicine and saved "
            "countless lives across the globe. Alexander Fleming observed that mold "
            "colonies inhibited bacterial growth in his laboratory petri dishes. "
            "This serendipitous finding led to the development of antibiotics that "
            "transformed the treatment of infectious diseases worldwide. Researchers "
            "subsequently developed numerous derivative compounds that expanded the "
            "therapeutic arsenal available to physicians treating serious infections."
        )
        profile = compute_retrieval_profile(text)
        assert profile["preferred_strategy"] == "vector"
        assert profile["content_density"] > 0.5
        assert profile["number_density"] < 0.2

    def test_tax_form_keyword_strategy(self):
        """Number-heavy tabular content should yield 'keyword' strategy."""
        text = (
            "1040 | 2025 | 123-45-6789\n"
            "---+---+---\n"
            "Line 1: 85,000.00 | 12,500.00 | 97,500.00\n"
            "Line 2: 24,000.00 | 3,600.00 | 27,600.00\n"
            "Line 3: 61,000.00 | 8,900.00 | 69,900.00\n"
            "Line 4: 15,213.50 | 2,100.75 | 17,314.25\n"
            "Line 5: 45,786.50 | 6,799.25 | 52,585.75\n"
            "Total: 230,000.00 | 33,900.00 | 263,900.00\n"
        )
        profile = compute_retrieval_profile(text)
        assert profile["preferred_strategy"] == "keyword"
        # Should have high table_ratio or number_density or low content_density
        assert (
            profile["table_ratio"] > 0.3
            or profile["number_density"] > 0.4
            or profile["content_density"] < 0.2
        )

    def test_mixed_document_not_keyword(self):
        """Content mixing prose and numbers should NOT yield 'keyword'."""
        text = (
            "The quarterly revenue reached approximately 4.2 million dollars. "
            "Our team expanded the customer base significantly during this period. "
            "We observed steady growth patterns in the northeastern market segment. "
            "The operating margin improved compared to the previous quarter results."
        )
        profile = compute_retrieval_profile(text)
        # Mostly prose text — strategy should be vector or balanced, never keyword
        assert profile["preferred_strategy"] in ("vector", "balanced")

    def test_code_snippet_classification(self):
        """Code snippets have low prose density, should not be 'vector'."""
        text = (
            "def compute(x, y):\n"
            "    result = x + y * 2\n"
            "    if result > 100:\n"
            "        return result - 50\n"
            "    return result\n"
            "\n"
            "for i in range(10):\n"
            "    print(compute(i, i + 1))\n"
        )
        profile = compute_retrieval_profile(text)
        # Code lacks proper prose sentences, so content_density should be low
        assert profile["content_density"] < 0.5
        assert profile["preferred_strategy"] != "vector"

    def test_all_profile_keys_present(self):
        """Every profile must contain the six canonical keys."""
        expected_keys = {
            "content_density",
            "keyword_richness",
            "table_ratio",
            "number_density",
            "preferred_strategy",
            "score",
        }
        profile = compute_retrieval_profile("Some text here.")
        assert set(profile.keys()) == expected_keys

    def test_parser_table_count_overrides_regex(self):
        """When table_count and page_count are provided, they override regex."""
        text = "This is a simple paragraph with no table markers at all."
        profile_no_meta = compute_retrieval_profile(text)
        profile_with_meta = compute_retrieval_profile(
            text, table_count=5, page_count=2
        )
        # 5 tables / (2 pages * 2) = 1.0 (capped), much higher than regex-based
        assert profile_with_meta["table_ratio"] > profile_no_meta["table_ratio"]


# ---------------------------------------------------------------------------
# Tests: get_hybrid_weights
# ---------------------------------------------------------------------------


class TestGetHybridWeights:
    def test_keyword_strategy(self):
        profile = {"preferred_strategy": "keyword"}
        assert get_hybrid_weights(profile) == (0.3, 0.7)

    def test_vector_strategy(self):
        profile = {"preferred_strategy": "vector"}
        assert get_hybrid_weights(profile) == (0.7, 0.3)

    def test_balanced_strategy_uses_defaults(self):
        profile = {"preferred_strategy": "balanced"}
        assert get_hybrid_weights(profile) == (0.5, 0.5)

    def test_none_profile_uses_defaults(self):
        assert get_hybrid_weights(None) == (0.5, 0.5)


# ---------------------------------------------------------------------------
# Tests: get_rerank_weights
# ---------------------------------------------------------------------------


class TestGetRerankWeights:
    def test_keyword_strategy(self):
        profile = {"preferred_strategy": "keyword"}
        assert get_rerank_weights(profile) == (0.2, 0.8)

    def test_vector_strategy(self):
        profile = {"preferred_strategy": "vector"}
        assert get_rerank_weights(profile) == (0.5, 0.5)

    def test_balanced_strategy_uses_defaults(self):
        profile = {"preferred_strategy": "balanced"}
        assert get_rerank_weights(profile) == (0.4, 0.6)

    def test_none_profile_uses_defaults(self):
        assert get_rerank_weights(None) == (0.4, 0.6)


# ---------------------------------------------------------------------------
# Tests: serialize_profile / deserialize_profile
# ---------------------------------------------------------------------------


class TestSerializeDeserialize:
    def test_round_trip_preserves_data(self):
        original = {
            "content_density": 0.75,
            "keyword_richness": 0.42,
            "table_ratio": 0.1,
            "number_density": 0.05,
            "preferred_strategy": "vector",
            "score": 0.82,
        }
        serialized = serialize_profile(original)
        restored = deserialize_profile(serialized)
        assert restored == original

    def test_deserialize_none_returns_none(self):
        assert deserialize_profile(None) is None

    def test_deserialize_empty_string_returns_none(self):
        assert deserialize_profile("") is None

    def test_deserialize_invalid_json_returns_none(self):
        assert deserialize_profile("not valid json {{{") is None
