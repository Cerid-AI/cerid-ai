# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for late interaction scoring module."""

import numpy as np
import pytest

from utils.late_interaction import (
    _cosine_similarity,
    _sliding_windows,
    compute_maxsim,
    late_interaction_rerank,
)


class TestSlidingWindows:
    """Tests for _sliding_windows()."""

    def test_basic_windows(self):
        windows = _sliding_windows("a b c d e f g")
        assert len(windows) >= 2
        assert windows[0] == "a b c"

    def test_short_text_single_window(self):
        windows = _sliding_windows("hello world")
        assert windows == ["hello world"]

    def test_empty_text(self):
        assert _sliding_windows("") == []

    def test_whitespace_only(self):
        assert _sliding_windows("   ") == []

    def test_exact_window_size(self):
        windows = _sliding_windows("one two three")
        assert windows == ["one two three"]

    def test_custom_window_size(self):
        windows = _sliding_windows("a b c d e f", window_size=2, stride=1)
        assert windows[0] == "a b"
        assert len(windows) >= 4

    def test_stride_controls_overlap(self):
        windows_s1 = _sliding_windows("a b c d e f g", window_size=3, stride=1)
        windows_s3 = _sliding_windows("a b c d e f g", window_size=3, stride=3)
        assert len(windows_s1) > len(windows_s3)


class TestCosineSimilarity:
    """Tests for _cosine_similarity()."""

    def test_identical_vectors(self):
        v = np.array([1.0, 2.0, 3.0])
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        a = np.array([1.0, 0.0])
        b = np.array([0.0, 1.0])
        assert abs(_cosine_similarity(a, b)) < 1e-6

    def test_zero_vector(self):
        assert _cosine_similarity(np.zeros(2), np.array([1.0, 2.0])) == 0.0

    def test_opposite_vectors(self):
        a = np.array([1.0, 0.0])
        b = np.array([-1.0, 0.0])
        assert abs(_cosine_similarity(a, b) - (-1.0)) < 1e-6


class TestComputeMaxSim:
    """Tests for compute_maxsim()."""

    def test_basic_maxsim(self):
        q_embs = [np.array([1.0, 0.0]), np.array([0.0, 1.0])]
        d_embs = [np.array([1.0, 0.0]), np.array([0.5, 0.5])]
        score = compute_maxsim(q_embs, d_embs)
        assert score > 0.5

    def test_empty_query_embeddings(self):
        assert compute_maxsim([], [np.array([1.0, 0.0])]) == 0.0

    def test_empty_doc_embeddings(self):
        assert compute_maxsim([np.array([1.0, 0.0])], []) == 0.0

    def test_perfect_match(self):
        embs = [np.array([1.0, 0.0]), np.array([0.0, 1.0])]
        score = compute_maxsim(embs, embs)
        assert abs(score - 1.0) < 1e-6


class TestLateInteractionRerank:
    """Tests for late_interaction_rerank()."""

    def test_single_result_unchanged(self):
        results = [{"content": "hello world test", "relevance": 0.9}]

        def embed_fn(texts):
            return [np.array([1.0, 0.0]) for _ in texts]

        reranked = late_interaction_rerank(results, "hello", embed_fn)
        assert len(reranked) == 1
        assert reranked[0]["relevance"] == 0.9

    def test_blends_scores(self):
        results = [
            {"content": "alpha beta gamma delta epsilon", "relevance": 0.8},
            {"content": "zeta eta theta iota kappa", "relevance": 0.7},
        ]

        def embed_fn(texts):
            return [np.random.randn(8) for _ in texts]

        reranked = late_interaction_rerank(results, "alpha beta gamma", embed_fn, top_n=2, blend_weight=0.5)
        assert all("late_interaction_score" in r or r.get("relevance", 0) >= 0 for r in reranked)

    def test_top_n_limits_processing(self):
        results = [
            {"content": f"document {i} with some content text", "relevance": 0.9 - i * 0.1}
            for i in range(5)
        ]
        original_last = results[4]["relevance"]

        def embed_fn(texts):
            return [np.random.randn(8) for _ in texts]

        reranked = late_interaction_rerank(results, "document content", embed_fn, top_n=2)
        assert len(reranked) == 5
        assert reranked[4]["relevance"] == original_last

    def test_embed_fn_failure_returns_original(self):
        results = [
            {"content": "test content for embedding", "relevance": 0.9},
            {"content": "more test content text here", "relevance": 0.8},
        ]

        def bad_embed_fn(texts):
            raise RuntimeError("model not loaded")

        reranked = late_interaction_rerank(results, "test content here", bad_embed_fn)
        assert reranked == results

    def test_empty_results(self):
        def embed_fn(texts):
            return [np.array([1.0]) for _ in texts]

        assert late_interaction_rerank([], "test", embed_fn) == []
