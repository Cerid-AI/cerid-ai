# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the public benchmark suite orchestrator."""

from __future__ import annotations

from app.eval.benchmark_suite import (
    DEFAULT_CATEGORIES,
    BenchmarkCategory,
    BenchmarkReport,
    BenchmarkResult,
    compare_reports,
    format_report,
)


def test_benchmark_category_creation():
    """BenchmarkCategory dataclass instantiation works with defaults."""
    cat = BenchmarkCategory(
        name="test_cat",
        description="A test category",
        dataset_path="/tmp/test.jsonl",
    )
    assert cat.name == "test_cat"
    assert cat.description == "A test category"
    assert cat.dataset_path == "/tmp/test.jsonl"
    assert cat.weight == 1.0


def test_benchmark_category_custom_weight():
    """BenchmarkCategory accepts a custom weight."""
    cat = BenchmarkCategory(
        name="weighted",
        description="Weighted category",
        dataset_path="/tmp/w.jsonl",
        weight=2.5,
    )
    assert cat.weight == 2.5


def test_benchmark_result_creation():
    """BenchmarkResult dataclass instantiation works."""
    result = BenchmarkResult(
        category="factual_recall",
        n_queries=20,
        avg_ndcg_5=0.85,
        avg_ndcg_10=0.80,
        avg_mrr=0.90,
        avg_precision_5=0.75,
        avg_recall_10=0.70,
        avg_latency_ms=45.3,
    )
    assert result.category == "factual_recall"
    assert result.n_queries == 20
    assert result.avg_ndcg_5 == 0.85
    assert result.avg_latency_ms == 45.3


def test_default_categories_count():
    """DEFAULT_CATEGORIES contains exactly 5 categories."""
    assert len(DEFAULT_CATEGORIES) == 5
    names = {cat.name for cat in DEFAULT_CATEGORIES}
    assert names == {"factual_recall", "multi_hop", "temporal", "cross_domain", "adversarial"}


def test_default_categories_have_valid_paths():
    """Each default category points to a .jsonl dataset file."""
    for cat in DEFAULT_CATEGORIES:
        assert cat.dataset_path.endswith(".jsonl"), f"{cat.name} path should end with .jsonl"
        assert cat.name in cat.dataset_path, f"{cat.name} should appear in its dataset path"


def test_format_report_markdown():
    """format_report produces valid Markdown with a table header and rows."""
    report = BenchmarkReport(
        results=[
            BenchmarkResult(
                category="factual_recall",
                n_queries=20,
                avg_ndcg_5=0.85,
                avg_ndcg_10=0.80,
                avg_mrr=0.90,
                avg_precision_5=0.75,
                avg_recall_10=0.70,
                avg_latency_ms=45.3,
            ),
            BenchmarkResult(
                category="multi_hop",
                n_queries=20,
                avg_ndcg_5=0.60,
                avg_ndcg_10=0.55,
                avg_mrr=0.65,
                avg_precision_5=0.50,
                avg_recall_10=0.45,
                avg_latency_ms=78.1,
            ),
        ],
        overall_score=0.72,
        timestamp="2026-03-27T12:00:00+00:00",
        pipeline="hybrid_reranked",
    )

    md = format_report(report)

    # Should contain Markdown table markers
    assert "| Category |" in md
    assert "|-------" in md
    assert "factual_recall" in md
    assert "multi_hop" in md
    assert "hybrid_reranked" in md
    assert "0.7200" in md  # overall score
    # Check it has the right number of data rows
    table_rows = [line for line in md.splitlines() if line.startswith("| ") and "Category" not in line and "---" not in line]
    assert len(table_rows) == 2


def test_format_report_empty():
    """format_report handles an empty report without errors."""
    report = BenchmarkReport(
        results=[],
        overall_score=0.0,
        timestamp="2026-03-27T12:00:00+00:00",
        pipeline="vector_only",
    )
    md = format_report(report)
    assert "vector_only" in md
    assert "0.0000" in md


def test_compare_reports():
    """compare_reports shows metric deltas between two reports."""
    result_a = BenchmarkResult(
        category="factual_recall",
        n_queries=20,
        avg_ndcg_5=0.80,
        avg_ndcg_10=0.75,
        avg_mrr=0.85,
        avg_precision_5=0.70,
        avg_recall_10=0.65,
        avg_latency_ms=50.0,
    )
    result_b = BenchmarkResult(
        category="factual_recall",
        n_queries=20,
        avg_ndcg_5=0.90,
        avg_ndcg_10=0.85,
        avg_mrr=0.92,
        avg_precision_5=0.80,
        avg_recall_10=0.75,
        avg_latency_ms=42.0,
    )

    report_a = BenchmarkReport(
        results=[result_a],
        overall_score=0.78,
        timestamp="2026-03-01T00:00:00+00:00",
        pipeline="hybrid",
    )
    report_b = BenchmarkReport(
        results=[result_b],
        overall_score=0.87,
        timestamp="2026-03-27T00:00:00+00:00",
        pipeline="hybrid_reranked",
    )

    diff = compare_reports(report_a, report_b)

    assert "Comparison" in diff
    assert "factual_recall" in diff
    # Should show positive deltas
    assert "+0.1000" in diff  # NDCG@5 delta
    assert "+0.0700" in diff  # MRR delta
    # Should show the overall delta
    assert "0.7800" in diff
    assert "0.8700" in diff
