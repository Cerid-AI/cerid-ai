# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for MMR diversity reordering module."""

import pytest

from utils.diversity import _extract_terms, jaccard_similarity, mmr_reorder


class TestExtractTerms:
    """Tests for _extract_terms()."""

    def test_basic_extraction(self):
        terms = _extract_terms("The quick brown fox jumps over the lazy dog")
        assert "quick" in terms
        assert "brown" in terms
        assert "fox" in terms

    def test_stopwords_removed(self):
        terms = _extract_terms("the is a an of in for on with at by")
        assert len(terms) == 0

    def test_short_words_removed(self):
        terms = _extract_terms("is it ok to go do")
        assert len(terms) == 0

    def test_suffix_stripping(self):
        terms = _extract_terms("running jumping processing completed")
        # After stripping -ing/-ed: runn, jump, process, complet
        assert any("jump" in t for t in terms)
        assert any("process" in t for t in terms)

    def test_empty_text(self):
        terms = _extract_terms("")
        assert len(terms) == 0


class TestJaccardSimilarity:
    """Tests for jaccard_similarity()."""

    def test_identical_sets(self):
        s = frozenset({"a", "b", "c"})
        assert jaccard_similarity(s, s) == 1.0

    def test_disjoint_sets(self):
        a = frozenset({"a", "b"})
        b = frozenset({"c", "d"})
        assert jaccard_similarity(a, b) == 0.0

    def test_partial_overlap(self):
        a = frozenset({"a", "b", "c"})
        b = frozenset({"b", "c", "d"})
        assert jaccard_similarity(a, b) == 0.5

    def test_empty_sets(self):
        assert jaccard_similarity(frozenset(), frozenset({"a"})) == 0.0
        assert jaccard_similarity(frozenset({"a"}), frozenset()) == 0.0
        assert jaccard_similarity(frozenset(), frozenset()) == 0.0


class TestMmrReorder:
    """Tests for mmr_reorder()."""

    def test_empty_results(self):
        assert mmr_reorder([], "test query") == []

    def test_single_result(self):
        results = [{"content": "hello world example", "relevance": 0.9}]
        reordered = mmr_reorder(results, "hello")
        assert len(reordered) == 1
        assert reordered[0]["relevance"] == 0.9

    def test_diverse_results_promoted(self):
        results = [
            {"content": "machine learning neural networks deep learning", "relevance": 0.9},
            {"content": "machine learning neural networks deep learning models", "relevance": 0.85},
            {"content": "database optimization query performance indexes", "relevance": 0.8},
        ]
        reordered = mmr_reorder(results, "machine learning and databases")
        contents = [r["content"] for r in reordered]
        db_idx = next(i for i, c in enumerate(contents) if "database" in c)
        dup_idx = next(i for i, c in enumerate(contents) if "models" in c)
        assert db_idx < dup_idx

    def test_lambda_one_pure_relevance(self):
        results = [
            {"content": "alpha beta gamma", "relevance": 0.5},
            {"content": "alpha beta gamma delta", "relevance": 0.9},
            {"content": "epsilon zeta eta", "relevance": 0.7},
        ]
        reordered = mmr_reorder(results, "alpha", lambda_param=1.0)
        assert reordered[0]["relevance"] == 0.9

    def test_top_n_limits_output(self):
        results = [
            {"content": f"document number {i} content text here", "relevance": 0.5 + i * 0.1}
            for i in range(5)
        ]
        reordered = mmr_reorder(results, "document", top_n=3)
        assert len(reordered) == 3

    def test_missing_content_handled(self):
        results = [
            {"relevance": 0.9},
            {"content": "hello world example text", "relevance": 0.8},
        ]
        reordered = mmr_reorder(results, "hello")
        assert len(reordered) == 2

    def test_preserves_all_result_fields(self):
        results = [
            {"content": "alpha content text here", "relevance": 0.9, "artifact_id": "a1", "domain": "code"},
            {"content": "beta content text here", "relevance": 0.8, "artifact_id": "a2", "domain": "finance"},
        ]
        reordered = mmr_reorder(results, "alpha")
        assert all("artifact_id" in r for r in reordered)
        assert all("domain" in r for r in reordered)
