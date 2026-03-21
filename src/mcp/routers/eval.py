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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/eval", tags=["eval"])

_logger = logging.getLogger("ai-companion.eval-router")

# Benchmark directory relative to MCP source root
_EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"


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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/run", response_model=EvalRunResponse)
async def run_eval(body: EvalRunRequest) -> EvalRunResponse:
    """Run the evaluation harness against a benchmark file and return metrics."""
    _check_enabled()

    benchmark_path = _EVAL_DIR / body.benchmark
    if not benchmark_path.exists():
        raise HTTPException(status_code=404, detail=f"Benchmark file not found: {body.benchmark}")
    if not benchmark_path.suffix == ".jsonl":
        raise HTTPException(status_code=400, detail="Benchmark file must be .jsonl format")

    from eval.harness import evaluate, load_benchmark, summarize, summarize_by_domain

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
    """List available benchmark files from the eval/ directory."""
    _check_enabled()

    if not _EVAL_DIR.exists():
        return []

    files: list[BenchmarkFile] = []
    for p in sorted(_EVAL_DIR.glob("*.jsonl")):
        files.append(BenchmarkFile(name=p.name, path=str(p), size_bytes=p.stat().st_size))
    return files


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_enabled() -> None:
    """Raise 403 if eval is not enabled via env var."""
    if os.getenv("CERID_EVAL_ENABLED", "").lower() not in ("1", "true", "yes"):
        raise HTTPException(status_code=403, detail="Eval harness disabled — set CERID_EVAL_ENABLED=true")
