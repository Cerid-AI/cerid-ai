# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for retrieval evaluation metrics."""

import math

import pytest

from eval.metrics import (
    average_precision,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)


class TestPrecisionAtK:
    def test_all_relevant(self):
        assert precision_at_k(["a", "b", "c"], {"a", "b", "c"}, 3) == 1.0

    def test_none_relevant(self):
        assert precision_at_k(["a", "b", "c"], {"x", "y"}, 3) == 0.0

    def test_partial_relevant(self):
        assert precision_at_k(["a", "b", "c", "d"], {"a", "c"}, 4) == 0.5

    def test_k_smaller_than_list(self):
        assert precision_at_k(["a", "b", "c"], {"a", "b"}, 2) == 1.0

    def test_k_larger_than_list(self):
        # Only 2 results, k=5 → precision = 1/2
        assert precision_at_k(["a", "b"], {"a"}, 5) == 0.5

    def test_k_zero(self):
        assert precision_at_k(["a", "b"], {"a"}, 0) == 0.0

    def test_empty_results(self):
        assert precision_at_k([], {"a"}, 5) == 0.0

    def test_empty_relevant(self):
        assert precision_at_k(["a", "b"], set(), 2) == 0.0


class TestRecallAtK:
    def test_all_found(self):
        assert recall_at_k(["a", "b", "c"], {"a", "b"}, 3) == 1.0

    def test_none_found(self):
        assert recall_at_k(["x", "y"], {"a", "b"}, 2) == 0.0

    def test_partial_found(self):
        assert recall_at_k(["a", "x", "y"], {"a", "b"}, 3) == 0.5

    def test_empty_relevant(self):
        assert recall_at_k(["a", "b"], set(), 2) == 0.0

    def test_k_zero(self):
        assert recall_at_k(["a"], {"a"}, 0) == 0.0


class TestMRR:
    def test_first_result_relevant(self):
        assert mrr(["a", "b", "c"], {"a"}) == 1.0

    def test_second_result_relevant(self):
        assert mrr(["x", "a", "c"], {"a"}) == 0.5

    def test_third_result_relevant(self):
        assert mrr(["x", "y", "a"], {"a"}) == pytest.approx(1 / 3)

    def test_no_relevant_results(self):
        assert mrr(["x", "y", "z"], {"a"}) == 0.0

    def test_empty_results(self):
        assert mrr([], {"a"}) == 0.0

    def test_multiple_relevant_returns_first(self):
        # MRR is based on the first relevant result
        assert mrr(["x", "a", "b"], {"a", "b"}) == 0.5


class TestNDCG:
    def test_perfect_ranking(self):
        # All relevant items at top — NDCG = 1.0
        assert ndcg_at_k(["a", "b", "c"], {"a", "b"}, 3) == pytest.approx(1.0)

    def test_worst_ranking(self):
        # Relevant items at bottom positions
        result = ndcg_at_k(["x", "y", "a"], {"a"}, 3)
        assert 0 < result < 1.0

    def test_no_relevant_items(self):
        assert ndcg_at_k(["x", "y", "z"], {"a"}, 3) == 0.0

    def test_empty_relevant_set(self):
        assert ndcg_at_k(["a", "b"], set(), 3) == 0.0

    def test_k_zero(self):
        assert ndcg_at_k(["a", "b"], {"a"}, 0) == 0.0

    def test_single_relevant_at_top(self):
        # 1 relevant item at position 1 → DCG = 1/log2(2) = 1.0, IDCG = 1.0
        assert ndcg_at_k(["a"], {"a"}, 1) == pytest.approx(1.0)

    def test_single_relevant_at_position_2(self):
        # relevant at position 2 → DCG = 1/log2(3), IDCG = 1/log2(2) = 1.0
        expected = (1.0 / math.log2(3)) / 1.0
        assert ndcg_at_k(["x", "a"], {"a"}, 2) == pytest.approx(expected)


class TestAveragePrecision:
    def test_perfect_ranking(self):
        # All relevant at top
        assert average_precision(["a", "b", "x"], {"a", "b"}) == pytest.approx(1.0)

    def test_relevant_at_end(self):
        # Relevant items at positions 2 and 3 (0-indexed)
        # P@2 when first relevant found = 1/2, P@3 when second found = 2/3
        # AP = (1/2 + 2/3) / 2
        result = average_precision(["x", "a", "b"], {"a", "b"})
        assert result == pytest.approx((0.5 + 2 / 3) / 2)

    def test_no_relevant(self):
        assert average_precision(["x", "y"], {"a"}) == 0.0

    def test_empty_relevant(self):
        assert average_precision(["a", "b"], set()) == 0.0

    def test_single_relevant_at_top(self):
        assert average_precision(["a", "x", "y"], {"a"}) == pytest.approx(1.0)
