# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Evaluation harness API — run benchmarks and retrieve metrics via HTTP.

Gated behind ``CERID_EVAL_ENABLED`` env var (default: false).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/eval", tags=["eval"])

_logger = logging.getLogger("ai-companion.eval-router")

# Benchmark directory relative to MCP source root
_EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"
_DATASETS_DIR = _EVAL_DIR / "datasets"


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class EvalRunRequest(BaseModel):
    benchmark: str = Field(default="benchmark.jsonl", description="Benchmark file name in eval/ directory")
    pipeline: str = Field(default="hybrid_reranked", description="Pipeline config: hybrid_reranked | hybrid | vector_only")


class EvalQueryResult(BaseModel):
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


class EvalRunResponse(BaseModel):
    results: list[EvalQueryResult]
    summary: dict[str, Any]
    by_domain: dict[str, dict[str, Any]]


class BenchmarkFile(BaseModel):
    name: str
    path: str
    size_bytes: int


class AblationRequest(BaseModel):
    queries: list[str] = Field(description="List of query strings to test")
    configs: list[str] = Field(
        default=["baseline", "full"],
        description="Config names from PRESET_CONFIGS (e.g. baseline, full, only_adaptive_retrieval)",
    )
    run_ragas: bool = Field(default=False, description="Run RAGAS metrics on each result (slower)")


class AblationResultRow(BaseModel):
    config: str
    query: str
    latency_s: float = 0.0
    result_count: int = 0
    ragas_scores: dict[str, float] = Field(default_factory=dict)


class AblationResponse(BaseModel):
    table: list[dict[str, Any]]
    n_configs: int
    n_queries: int


class RagasRequest(BaseModel):
    question: str = Field(description="The question that was asked")
    answer: str = Field(description="The generated answer")
    contexts: list[str] = Field(description="Retrieved context passages")


class RagasScoreResult(BaseModel):
    score: float
    reasoning: str


class RagasResponse(BaseModel):
    faithfulness: RagasScoreResult
    answer_relevancy: RagasScoreResult
    context_precision: RagasScoreResult
    context_recall: RagasScoreResult


class LeaderboardEntryResponse(BaseModel):
    pipeline: str
    composite_score: float
    avg_ndcg_5: float = 0.0
    avg_ndcg_10: float = 0.0
    avg_mrr: float = 0.0
    avg_precision_5: float = 0.0
    avg_recall_10: float = 0.0
    avg_faithfulness: float = 0.0
    avg_answer_relevancy: float = 0.0
    avg_context_precision: float = 0.0
    avg_context_recall: float = 0.0
    n_queries: int = 0
    benchmark: str = ""
    timestamp: str = ""


class CompareResponse(BaseModel):
    pipeline_a: str
    pipeline_b: str
    n_shared_queries: int
    summary_a: dict[str, Any]
    summary_b: dict[str, Any]
    metric_diffs: dict[str, Any]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/run", response_model=EvalRunResponse)
async def run_eval(body: EvalRunRequest) -> EvalRunResponse:
    """Run the evaluation harness against a benchmark file and return metrics."""
    _check_enabled()

    benchmark_path = (_EVAL_DIR / body.benchmark).resolve()
    if not benchmark_path.is_relative_to(_EVAL_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid benchmark path")
    if not benchmark_path.exists():
        raise HTTPException(status_code=404, detail=f"Benchmark file not found: {body.benchmark}")
    if not benchmark_path.suffix == ".jsonl":
        raise HTTPException(status_code=400, detail="Benchmark file must be .jsonl format")

    from app.eval.harness import evaluate, load_benchmark, summarize, summarize_by_domain

    queries = load_benchmark(benchmark_path)
    if not queries:
        raise HTTPException(status_code=400, detail="Benchmark file is empty or contains no valid queries")

    _logger.info("eval_run_started benchmark=%s pipeline=%s query_count=%d", body.benchmark, body.pipeline, len(queries))

    raw_results = await evaluate(queries, pipeline=body.pipeline)
    summary = summarize(raw_results)
    by_domain = summarize_by_domain(raw_results)

    results = [
        EvalQueryResult(
            query=r.query,
            pipeline=r.pipeline,
            domain=r.domain,
            ndcg_5=r.ndcg_5,
            ndcg_10=r.ndcg_10,
            mrr=r.mrr,
            precision_5=r.precision_5,
            recall_10=r.recall_10,
            avg_precision=r.avg_precision,
            total_results=r.total_results,
            confidence=r.confidence,
            latency_ms=r.latency_ms,
        )
        for r in raw_results
    ]

    _logger.info("eval_run_completed benchmark=%s n=%d", body.benchmark, len(results))

    return EvalRunResponse(results=results, summary=summary, by_domain=by_domain)


@router.get("/benchmarks", response_model=list[BenchmarkFile])
async def list_benchmarks() -> list[BenchmarkFile]:
    """List available benchmark files from the eval/ and eval/datasets/ directories."""
    _check_enabled()

    files: list[BenchmarkFile] = []
    for search_dir in [_EVAL_DIR, _DATASETS_DIR]:
        if not search_dir.exists():
            continue
        for p in sorted(search_dir.glob("*.jsonl")):
            files.append(BenchmarkFile(name=p.name, path=str(p.relative_to(_EVAL_DIR.resolve())), size_bytes=p.stat().st_size))
    return files


@router.post("/ablation", response_model=AblationResponse)
async def run_ablation_endpoint(body: AblationRequest) -> AblationResponse:
    """Run an ablation study across toggle configurations."""
    _check_enabled()

    from app.eval.ablation import PRESET_CONFIGS, results_to_table, run_ablation

    # Resolve config names to AblationConfig objects
    config_map = {c.name: c for c in PRESET_CONFIGS}
    selected = []
    for name in body.configs:
        if name not in config_map:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown ablation config: {name!r}. Available: {list(config_map.keys())}",
            )
        selected.append(config_map[name])

    _logger.info("ablation_started configs=%s queries=%d run_ragas=%s", body.configs, len(body.queries), body.run_ragas)

    results = await run_ablation(
        queries=body.queries,
        configs=selected,
        run_ragas=body.run_ragas,
    )
    table = results_to_table(results)

    return AblationResponse(table=table, n_configs=len(selected), n_queries=len(body.queries))


@router.get("/leaderboard", response_model=list[LeaderboardEntryResponse])
async def get_leaderboard_endpoint() -> list[LeaderboardEntryResponse]:
    """Get the top 20 leaderboard entries sorted by composite score."""
    _check_enabled()

    from app.eval.leaderboard import get_leaderboard

    redis_client = _get_redis()
    entries = get_leaderboard(redis_client, top_k=20)

    return [
        LeaderboardEntryResponse(
            pipeline=e.pipeline,
            composite_score=round(e.composite_score, 4),
            avg_ndcg_5=e.avg_ndcg_5,
            avg_ndcg_10=e.avg_ndcg_10,
            avg_mrr=e.avg_mrr,
            avg_precision_5=e.avg_precision_5,
            avg_recall_10=e.avg_recall_10,
            avg_faithfulness=e.avg_faithfulness,
            avg_answer_relevancy=e.avg_answer_relevancy,
            avg_context_precision=e.avg_context_precision,
            avg_context_recall=e.avg_context_recall,
            n_queries=e.n_queries,
            benchmark=e.benchmark,
            timestamp=e.timestamp,
        )
        for e in entries
    ]


@router.post("/ragas", response_model=RagasResponse)
async def run_ragas_endpoint(body: RagasRequest) -> RagasResponse:
    """Run all four RAGAS metrics on a single question/answer/contexts triple."""
    _check_enabled()

    from app.eval.ragas_metrics import evaluate_all

    _logger.info("ragas_eval_started question_len=%d contexts=%d", len(body.question), len(body.contexts))

    try:
        results = await evaluate_all(
            question=body.question,
            answer=body.answer,
            contexts=body.contexts,
        )
    except Exception as e:
        _logger.error("ragas_eval_failed: %s", e)
        raise HTTPException(status_code=500, detail=f"RAGAS evaluation failed: {e}")

    return RagasResponse(
        faithfulness=RagasScoreResult(score=results["faithfulness"].score, reasoning=results["faithfulness"].reasoning),
        answer_relevancy=RagasScoreResult(score=results["answer_relevancy"].score, reasoning=results["answer_relevancy"].reasoning),
        context_precision=RagasScoreResult(score=results["context_precision"].score, reasoning=results["context_precision"].reasoning),
        context_recall=RagasScoreResult(score=results["context_recall"].score, reasoning=results["context_recall"].reasoning),
    )


@router.get("/compare", response_model=CompareResponse)
async def compare_pipelines_endpoint(
    pipeline_a: str = Query(default="hybrid_reranked", description="First pipeline config"),
    pipeline_b: str = Query(default="vector_only", description="Second pipeline config"),
    benchmark: str = Query(default="benchmark.jsonl", description="Benchmark file name"),
) -> CompareResponse:
    """Compare two pipeline configurations head-to-head on a benchmark."""
    _check_enabled()

    from app.eval.harness import compare_pipelines, evaluate, load_benchmark

    # Try datasets/ subdirectory first, then eval/ root
    benchmark_path = (_DATASETS_DIR / benchmark).resolve()
    if not benchmark_path.is_relative_to(_EVAL_DIR.resolve()):
        raise HTTPException(status_code=400, detail="Invalid benchmark path")
    if not benchmark_path.exists():
        benchmark_path = (_EVAL_DIR / benchmark).resolve()
        if not benchmark_path.is_relative_to(_EVAL_DIR.resolve()):
            raise HTTPException(status_code=400, detail="Invalid benchmark path")
    if not benchmark_path.exists():
        raise HTTPException(status_code=404, detail=f"Benchmark file not found: {benchmark}")

    queries = load_benchmark(benchmark_path)
    if not queries:
        raise HTTPException(status_code=400, detail="Benchmark file is empty or contains no valid queries")

    _logger.info(
        "compare_started pipeline_a=%s pipeline_b=%s benchmark=%s queries=%d",
        pipeline_a, pipeline_b, benchmark, len(queries),
    )

    results_a = await evaluate(queries, pipeline=pipeline_a)
    results_b = await evaluate(queries, pipeline=pipeline_b)
    comparison = compare_pipelines(results_a, results_b)

    return CompareResponse(
        pipeline_a=comparison.get("pipeline_a", pipeline_a),
        pipeline_b=comparison.get("pipeline_b", pipeline_b),
        n_shared_queries=comparison.get("n_shared_queries", 0),
        summary_a=comparison.get("summary_a", {}),
        summary_b=comparison.get("summary_b", {}),
        metric_diffs=comparison.get("metric_diffs", {}),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_enabled() -> None:
    """Raise 403 if eval is not enabled via env var."""
    if os.getenv("CERID_EVAL_ENABLED", "").lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=403, detail="Eval harness disabled — set CERID_EVAL_ENABLED=true")


def _get_redis() -> Any:
    """Get the Redis client, returning None if unavailable."""
    try:
        from app.deps import get_redis
        return get_redis()
    except Exception:
        _logger.warning("Redis unavailable for leaderboard")
        return None
