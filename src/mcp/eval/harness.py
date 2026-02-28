# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Retrieval evaluation harness.

Runs benchmark queries against the RAG pipeline and computes metrics.
Supports different pipeline configurations for A/B comparison.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from eval.metrics import average_precision, mrr, ndcg_at_k, precision_at_k, recall_at_k

logger = logging.getLogger("ai-companion.eval")


@dataclass
class EvalQuery:
    """A benchmark query with known relevant artifacts."""

    query: str
    relevant_artifact_ids: Set[str]
    domain: Optional[str] = None


@dataclass
class EvalResult:
    """Evaluation result for a single query."""

    query: str
    pipeline: str
    ndcg_5: float = 0.0
    ndcg_10: float = 0.0
    mrr: float = 0.0
    precision_5: float = 0.0
    recall_10: float = 0.0
    avg_precision: float = 0.0
    total_results: int = 0
    confidence: float = 0.0


def load_benchmark(path: str | Path) -> List[EvalQuery]:
    """Load benchmark queries from a JSONL file.

    Each line: {"query": "...", "relevant_ids": ["id1", "id2"], "domain": "..."}
    """
    queries: List[EvalQuery] = []
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
    queries: List[EvalQuery],
    pipeline: str = "hybrid_reranked",
    chroma_client: Any = None,
    redis_client: Any = None,
    neo4j_driver: Any = None,
) -> List[EvalResult]:
    """Run evaluation queries through the retrieval pipeline and score results.

    Pipeline options:
      - "hybrid_reranked": Full pipeline (vector + BM25 + graph + rerank)
      - "hybrid": Vector + BM25, no reranking
      - "vector_only": Vector search only (no BM25)
    """
    from agents.query_agent import agent_query

    results: List[EvalResult] = []

    for eq in queries:
        use_reranking = pipeline == "hybrid_reranked"
        domains = [eq.domain] if eq.domain else None

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
            results.append(EvalResult(query=eq.query, pipeline=pipeline))
            continue

        ranked_ids = [
            r["artifact_id"] for r in response.get("results", [])
        ]
        relevant = eq.relevant_artifact_ids

        results.append(
            EvalResult(
                query=eq.query,
                pipeline=pipeline,
                ndcg_5=round(ndcg_at_k(ranked_ids, relevant, 5), 4),
                ndcg_10=round(ndcg_at_k(ranked_ids, relevant, 10), 4),
                mrr=round(mrr(ranked_ids, relevant), 4),
                precision_5=round(precision_at_k(ranked_ids, relevant, 5), 4),
                recall_10=round(recall_at_k(ranked_ids, relevant, 10), 4),
                avg_precision=round(average_precision(ranked_ids, relevant), 4),
                total_results=response.get("total_results", 0),
                confidence=round(response.get("confidence", 0.0), 4),
            )
        )

    return results


def summarize(results: List[EvalResult]) -> Dict[str, Any]:
    """Compute aggregate metrics across all evaluation results."""
    if not results:
        return {"n": 0}

    n = len(results)
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
    }
