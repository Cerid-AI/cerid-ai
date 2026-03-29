# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Retrieval evaluation harness — **development / debugging tool**.

Runs benchmark queries against the RAG pipeline and computes metrics.
Supports different pipeline configurations for A/B comparison with
latency tracking, per-domain breakdowns, and statistical significance.

Wired into ``routers/eval.py`` (``POST /api/eval/run``, ``GET /api/eval/benchmarks``).
Gated behind ``CERID_EVAL_ENABLED=true``.  Can also be run directly via
``python -m eval.harness`` from the MCP container.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval.metrics import average_precision, mrr, ndcg_at_k, precision_at_k, recall_at_k

logger = logging.getLogger("ai-companion.eval")


@dataclass
class EvalQuery:
    """A benchmark query with known relevant artifacts."""

    query: str
    relevant_artifact_ids: set[str]
    domain: str | None = None


@dataclass
class EvalResult:
    """Evaluation result for a single query."""

    query: str
    pipeline: str
    domain: str | None = None
    ndcg_5: float = 0.0
    ndcg_10: float = 0.0
    mrr: float = 0.0
    precision_5: float = 0.0
    recall_10: float = 0.0
    avg_precision: float = 0.0
    total_results: int = 0
    confidence: float = 0.0
    latency_ms: float = 0.0


def load_benchmark(path: str | Path) -> list[EvalQuery]:
    """Load benchmark queries from a JSONL file.

    Each line: {"query": "...", "relevant_ids": ["id1", "id2"], "domain": "..."}
    """
    queries: list[EvalQuery] = []
    p = Path(path)
    if not p.exists():
        logger.warning(f"Benchmark file not found: {p}")
        return queries

    with open(p) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                queries.append(
                    EvalQuery(
                        query=entry["query"],
                        relevant_artifact_ids=set(entry.get("relevant_ids", [])),
                        domain=entry.get("domain"),
                    )
                )
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Skipping benchmark line {line_num}: {e}")

    return queries


async def evaluate(
    queries: list[EvalQuery],
    pipeline: str = "hybrid_reranked",
    chroma_client: Any = None,
    redis_client: Any = None,
    neo4j_driver: Any = None,
) -> list[EvalResult]:
    """Run evaluation queries through the retrieval pipeline and score results.

    Pipeline options:
      - "hybrid_reranked": Full pipeline (vector + BM25 + graph + rerank)
      - "hybrid": Vector + BM25, no reranking
      - "vector_only": Vector search only (no BM25)
    """
    from agents.query_agent import agent_query

    results: list[EvalResult] = []

    for eq in queries:
        use_reranking = pipeline == "hybrid_reranked"
        domains = [eq.domain] if eq.domain else None

        t0 = time.perf_counter()
        try:
            response = await agent_query(
                query=eq.query,
                domains=domains,
                top_k=20,
                use_reranking=use_reranking,
                chroma_client=chroma_client,
                redis_client=redis_client,
                neo4j_driver=neo4j_driver,
            )
        except Exception as e:
            logger.error(f"Eval query failed: {eq.query!r} — {e}")
            results.append(EvalResult(query=eq.query, pipeline=pipeline, domain=eq.domain))
            continue
        latency_ms = (time.perf_counter() - t0) * 1000

        ranked_ids = [
            r["artifact_id"] for r in response.get("results", [])
        ]
        relevant = eq.relevant_artifact_ids

        results.append(
            EvalResult(
                query=eq.query,
                pipeline=pipeline,
                domain=eq.domain,
                ndcg_5=round(ndcg_at_k(ranked_ids, relevant, 5), 4),
                ndcg_10=round(ndcg_at_k(ranked_ids, relevant, 10), 4),
                mrr=round(mrr(ranked_ids, relevant), 4),
                precision_5=round(precision_at_k(ranked_ids, relevant, 5), 4),
                recall_10=round(recall_at_k(ranked_ids, relevant, 10), 4),
                avg_precision=round(average_precision(ranked_ids, relevant), 4),
                total_results=response.get("total_results", 0),
                confidence=round(response.get("confidence", 0.0), 4),
                latency_ms=round(latency_ms, 1),
            )
        )

    return results


def _compute_percentiles(values: list[float]) -> dict[str, float]:
    """Compute P50, P95, P99 from a list of values."""
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
    sv = sorted(values)
    n = len(sv)

    def _pct(p: float) -> float:
        idx = (p / 100) * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        return round(sv[lo] * (1 - frac) + sv[hi] * frac, 1)

    return {"p50": _pct(50), "p95": _pct(95), "p99": _pct(99)}


def summarize(results: list[EvalResult]) -> dict[str, Any]:
    """Compute aggregate metrics across all evaluation results."""
    if not results:
        return {"n": 0}

    n = len(results)
    latencies = [r.latency_ms for r in results]

    return {
        "n": n,
        "pipeline": results[0].pipeline,
        "avg_ndcg_5": round(sum(r.ndcg_5 for r in results) / n, 4),
        "avg_ndcg_10": round(sum(r.ndcg_10 for r in results) / n, 4),
        "avg_mrr": round(sum(r.mrr for r in results) / n, 4),
        "avg_precision_5": round(sum(r.precision_5 for r in results) / n, 4),
        "avg_recall_10": round(sum(r.recall_10 for r in results) / n, 4),
        "avg_avg_precision": round(sum(r.avg_precision for r in results) / n, 4),
        "avg_confidence": round(sum(r.confidence for r in results) / n, 4),
        "latency": _compute_percentiles(latencies),
    }


def summarize_by_domain(results: list[EvalResult]) -> dict[str, dict[str, Any]]:
    """Compute per-domain metric breakdowns."""
    by_domain: dict[str, list[EvalResult]] = defaultdict(list)
    for r in results:
        domain = r.domain or "all"
        by_domain[domain].append(r)

    return {domain: summarize(domain_results) for domain, domain_results in by_domain.items()}


def compare_pipelines(
    results_a: list[EvalResult],
    results_b: list[EvalResult],
) -> dict[str, Any]:
    """Compare two pipeline runs with paired difference analysis.

    Returns per-metric differences and indicates which pipeline wins.
    Uses paired differences (matched by query) for fair comparison.
    """
    summary_a = summarize(results_a)
    summary_b = summarize(results_b)

    # Build query-matched pairs
    map_a = {r.query: r for r in results_a}
    map_b = {r.query: r for r in results_b}
    shared_queries = set(map_a.keys()) & set(map_b.keys())

    metrics = ["ndcg_5", "ndcg_10", "mrr", "precision_5", "recall_10", "avg_precision"]
    diffs: dict[str, list[float]] = {m: [] for m in metrics}

    for q in shared_queries:
        ra, rb = map_a[q], map_b[q]
        for m in metrics:
            diffs[m].append(getattr(ra, m) - getattr(rb, m))

    comparison: dict[str, Any] = {
        "pipeline_a": summary_a.get("pipeline", "A"),
        "pipeline_b": summary_b.get("pipeline", "B"),
        "n_shared_queries": len(shared_queries),
        "summary_a": summary_a,
        "summary_b": summary_b,
        "metric_diffs": {},
    }

    for m in metrics:
        d = diffs[m]
        if not d:
            continue
        mean_diff = sum(d) / len(d)
        # Simple sign test: fraction of queries where A > B
        wins_a = sum(1 for x in d if x > 0)
        wins_b = sum(1 for x in d if x < 0)
        comparison["metric_diffs"][m] = {
            "mean_diff": round(mean_diff, 4),
            "wins_a": wins_a,
            "wins_b": wins_b,
            "ties": len(d) - wins_a - wins_b,
            "winner": "A" if mean_diff > 0.001 else ("B" if mean_diff < -0.001 else "tie"),
        }

    return comparison
