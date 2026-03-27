# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Public benchmark suite — orchestrates multi-category evaluation runs.

Loads category-specific JSONL datasets, runs them through the eval harness,
and produces per-category and overall benchmark reports with Markdown output.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("ai-companion.eval.benchmark")

_DATASETS_DIR = Path(__file__).resolve().parent / "datasets"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkCategory:
    """A named benchmark category pointing to a dataset JSONL file."""

    name: str
    description: str
    dataset_path: str
    weight: float = 1.0


@dataclass
class BenchmarkResult:
    """Aggregate metrics for a single benchmark category."""

    category: str
    n_queries: int
    avg_ndcg_5: float
    avg_ndcg_10: float
    avg_mrr: float
    avg_precision_5: float
    avg_recall_10: float
    avg_latency_ms: float


@dataclass
class BenchmarkReport:
    """Full benchmark report across all categories."""

    results: list[BenchmarkResult] = field(default_factory=list)
    overall_score: float = 0.0
    timestamp: str = ""
    pipeline: str = ""


# ---------------------------------------------------------------------------
# Default categories
# ---------------------------------------------------------------------------

DEFAULT_CATEGORIES: list[BenchmarkCategory] = [
    BenchmarkCategory(
        name="factual_recall",
        description="Direct fact retrieval queries",
        dataset_path=str(_DATASETS_DIR / "factual_recall.jsonl"),
    ),
    BenchmarkCategory(
        name="multi_hop",
        description="Multi-step reasoning queries requiring information synthesis",
        dataset_path=str(_DATASETS_DIR / "multi_hop.jsonl"),
    ),
    BenchmarkCategory(
        name="temporal",
        description="Time-sensitive queries about recent changes and updates",
        dataset_path=str(_DATASETS_DIR / "temporal.jsonl"),
    ),
    BenchmarkCategory(
        name="cross_domain",
        description="Queries spanning multiple knowledge domains",
        dataset_path=str(_DATASETS_DIR / "cross_domain.jsonl"),
    ),
    BenchmarkCategory(
        name="adversarial",
        description="Robustness queries with negation, misleading premises, and unusual framing",
        dataset_path=str(_DATASETS_DIR / "adversarial.jsonl"),
    ),
]


# ---------------------------------------------------------------------------
# Suite runner
# ---------------------------------------------------------------------------


async def run_suite(
    pipeline: str = "hybrid_reranked",
    categories: list[BenchmarkCategory] | None = None,
) -> BenchmarkReport:
    """Run the full benchmark suite and return a consolidated report.

    Parameters
    ----------
    pipeline:
        Retrieval pipeline config passed to ``evaluate()``.
    categories:
        Override the default category list. Uses ``DEFAULT_CATEGORIES`` when *None*.
    """
    from app.eval.harness import evaluate, load_benchmark

    cats = categories or DEFAULT_CATEGORIES
    report = BenchmarkReport(
        pipeline=pipeline,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    total_weighted_score = 0.0
    total_weight = 0.0

    for cat in cats:
        queries = load_benchmark(cat.dataset_path)
        if not queries:
            logger.warning("No queries loaded for category %s — skipping", cat.name)
            continue

        eval_results = await evaluate(queries, pipeline=pipeline)

        n = len(eval_results)
        if n == 0:
            continue

        avg_ndcg_5 = sum(r.ndcg_5 for r in eval_results) / n
        avg_ndcg_10 = sum(r.ndcg_10 for r in eval_results) / n
        avg_mrr = sum(r.mrr for r in eval_results) / n
        avg_precision_5 = sum(r.precision_5 for r in eval_results) / n
        avg_recall_10 = sum(r.recall_10 for r in eval_results) / n
        avg_latency_ms = sum(r.latency_ms for r in eval_results) / n

        result = BenchmarkResult(
            category=cat.name,
            n_queries=n,
            avg_ndcg_5=round(avg_ndcg_5, 4),
            avg_ndcg_10=round(avg_ndcg_10, 4),
            avg_mrr=round(avg_mrr, 4),
            avg_precision_5=round(avg_precision_5, 4),
            avg_recall_10=round(avg_recall_10, 4),
            avg_latency_ms=round(avg_latency_ms, 1),
        )
        report.results.append(result)

        # Composite per-category: 40% NDCG@5 + 30% MRR + 30% P@5
        cat_score = 0.4 * avg_ndcg_5 + 0.3 * avg_mrr + 0.3 * avg_precision_5
        total_weighted_score += cat_score * cat.weight
        total_weight += cat.weight

    report.overall_score = round(total_weighted_score / total_weight, 4) if total_weight > 0 else 0.0
    return report


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_report(report: BenchmarkReport) -> str:
    """Render a benchmark report as a Markdown table."""
    lines = [
        f"## Benchmark Report — `{report.pipeline}`",
        f"**Timestamp:** {report.timestamp}  ",
        f"**Overall Score:** {report.overall_score:.4f}",
        "",
        "| Category | N | NDCG@5 | NDCG@10 | MRR | P@5 | R@10 | Latency (ms) |",
        "|----------|---|--------|---------|-----|-----|------|-------------|",
    ]

    for r in report.results:
        lines.append(
            f"| {r.category} | {r.n_queries} | {r.avg_ndcg_5:.4f} | "
            f"{r.avg_ndcg_10:.4f} | {r.avg_mrr:.4f} | {r.avg_precision_5:.4f} | "
            f"{r.avg_recall_10:.4f} | {r.avg_latency_ms:.1f} |"
        )

    return "\n".join(lines)


def compare_reports(a: BenchmarkReport, b: BenchmarkReport) -> str:
    """Return a Markdown table showing metric deltas between two reports."""
    lines = [
        f"## Benchmark Comparison",
        f"**A:** `{a.pipeline}` ({a.timestamp})  ",
        f"**B:** `{b.pipeline}` ({b.timestamp})  ",
        f"**Overall:** {a.overall_score:.4f} → {b.overall_score:.4f} "
        f"(delta: {b.overall_score - a.overall_score:+.4f})",
        "",
        "| Category | NDCG@5 (A→B) | MRR (A→B) | P@5 (A→B) | Latency (A→B) |",
        "|----------|-------------|----------|----------|--------------|",
    ]

    b_by_cat = {r.category: r for r in b.results}

    for ra in a.results:
        rb = b_by_cat.get(ra.category)
        if rb is None:
            lines.append(f"| {ra.category} | {ra.avg_ndcg_5:.4f} → — | — | — | — |")
            continue

        lines.append(
            f"| {ra.category} | "
            f"{ra.avg_ndcg_5:.4f} → {rb.avg_ndcg_5:.4f} ({rb.avg_ndcg_5 - ra.avg_ndcg_5:+.4f}) | "
            f"{ra.avg_mrr:.4f} → {rb.avg_mrr:.4f} ({rb.avg_mrr - ra.avg_mrr:+.4f}) | "
            f"{ra.avg_precision_5:.4f} → {rb.avg_precision_5:.4f} ({rb.avg_precision_5 - ra.avg_precision_5:+.4f}) | "
            f"{ra.avg_latency_ms:.1f} → {rb.avg_latency_ms:.1f} ({rb.avg_latency_ms - ra.avg_latency_ms:+.1f}) |"
        )

    return "\n".join(lines)
