# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Observability API — aggregated metrics, health score, cost breakdown, quality.

Provides real-time observability data for the React dashboard:

- ``GET /observability/metrics`` — aggregated stats for all metrics
- ``GET /observability/metrics/{name}`` — raw time series for a specific metric
- ``GET /observability/health-score`` — composite health score (0-100)
- ``GET /observability/cost`` — LLM cost breakdown by model
- ``GET /observability/quality`` — retrieval quality metrics
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger("ai-companion.observability")

router = APIRouter(prefix="/observability", tags=["observability"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class MetricAggregation(BaseModel):
    avg: float | None = None
    p50: float | None = None
    p95: float | None = None
    p99: float | None = None
    min: float | None = None
    max: float | None = None
    count: int | float = 0


class AggregatedMetricsResponse(BaseModel):
    window_minutes: int
    timestamp: str
    metrics: dict[str, MetricAggregation]


class TimeSeriesPoint(BaseModel):
    timestamp: float
    value: float
    tags: dict[str, str] = {}


class TimeSeriesResponse(BaseModel):
    name: str
    window_minutes: int
    points: list[TimeSeriesPoint]
    count: int


class HealthScoreResponse(BaseModel):
    score: int
    grade: str  # A/B/C/D/F
    factors: dict[str, dict[str, float | int | str | None]]
    timestamp: str


class CostBreakdownResponse(BaseModel):
    window_minutes: int
    total_cost_usd: float
    by_model: dict[str, float]
    timestamp: str


class QualityMetricsResponse(BaseModel):
    window_minutes: int
    retrieval_ndcg: MetricAggregation
    verification_accuracy: MetricAggregation
    cache_hit_rate: MetricAggregation
    timestamp: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    from utils.time import utcnow_iso
    return utcnow_iso()


def _get_collector():
    from utils.metrics import get_metrics_collector
    return get_metrics_collector()


def _compute_health_score(metrics: dict) -> tuple[int, str, dict]:
    """Compute a 0-100 health score from aggregated metrics.

    Factors:
    - Latency: p95 query latency (target < 2000ms = 100, > 10000ms = 0)
    - Cache: cache hit rate (target > 0.5 = 100, 0 = 0)
    - Verification: accuracy (target > 0.8 = 100, 0 = 0)
    - Throughput: queries per minute (any activity = positive signal)
    """
    factors: dict[str, dict[str, float | int | str | None]] = {}
    scores: list[float] = []

    # Latency factor (weight: 35%)
    latency = metrics.get("query_latency_ms", {})
    p95 = latency.get("p95")
    if p95 is not None and p95 > 0:
        # Linear scale: 0ms = 100, 10000ms = 0
        latency_score = max(0, min(100, 100 - (p95 / 100)))
        factors["latency"] = {"p95_ms": round(p95, 1), "score": round(latency_score, 1), "weight": 35}
        scores.append(latency_score * 0.35)
    else:
        factors["latency"] = {"p95_ms": None, "score": None, "weight": 35, "status": "no_data"}
        scores.append(70 * 0.35)  # Default to "decent" when no data

    # Cache hit rate factor (weight: 20%)
    cache = metrics.get("cache_hit_rate", {})
    cache_avg = cache.get("avg")
    if cache_avg is not None:
        cache_score = min(100, cache_avg * 100)
        factors["cache"] = {"hit_rate": round(cache_avg, 3), "score": round(cache_score, 1), "weight": 20}
        scores.append(cache_score * 0.20)
    else:
        factors["cache"] = {"hit_rate": None, "score": None, "weight": 20, "status": "no_data"}
        scores.append(50 * 0.20)

    # Verification accuracy factor (weight: 30%)
    verif = metrics.get("verification_accuracy", {})
    verif_avg = verif.get("avg")
    if verif_avg is not None:
        verif_score = min(100, verif_avg * 100)
        factors["verification"] = {"accuracy": round(verif_avg, 3), "score": round(verif_score, 1), "weight": 30}
        scores.append(verif_score * 0.30)
    else:
        factors["verification"] = {"accuracy": None, "score": None, "weight": 30, "status": "no_data"}
        scores.append(70 * 0.30)

    # Throughput factor (weight: 15%)
    throughput = metrics.get("queries_per_minute", {})
    qpm_count = throughput.get("count", 0)
    if qpm_count and qpm_count > 0:
        # Any activity is good; more is better up to a point
        tp_score = min(100, qpm_count * 2)  # 50 queries in window = 100
        factors["throughput"] = {"query_count": qpm_count, "score": round(tp_score, 1), "weight": 15}
        scores.append(tp_score * 0.15)
    else:
        factors["throughput"] = {"query_count": 0, "score": None, "weight": 15, "status": "no_data"}
        scores.append(30 * 0.15)

    total = int(round(sum(scores)))
    total = max(0, min(100, total))

    if total >= 90:
        grade = "A"
    elif total >= 75:
        grade = "B"
    elif total >= 60:
        grade = "C"
    elif total >= 40:
        grade = "D"
    else:
        grade = "F"

    return total, grade, factors


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/metrics", response_model=AggregatedMetricsResponse)
def get_aggregated_metrics(
    window: int = Query(60, ge=1, le=10080, alias="window_minutes"),
):
    """Return aggregated metrics (avg, p50, p95, p99) for all known metrics."""
    collector = _get_collector()
    raw = collector.get_aggregated_metrics(window)
    metrics = {name: MetricAggregation(**agg) for name, agg in raw.items()}
    return AggregatedMetricsResponse(
        window_minutes=window,
        timestamp=_iso_now(),
        metrics=metrics,
    )


@router.get("/metrics/{name}", response_model=TimeSeriesResponse)
def get_metric_timeseries(
    name: str,
    window: int = Query(60, ge=1, le=10080, alias="window_minutes"),
):
    """Return raw time-series data points for a specific metric."""
    from utils.metrics import METRIC_NAMES
    if name not in METRIC_NAMES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown metric: {name}. Valid: {', '.join(sorted(METRIC_NAMES))}",
        )

    collector = _get_collector()
    points = collector.get_metrics(name, window)
    return TimeSeriesResponse(
        name=name,
        window_minutes=window,
        points=[TimeSeriesPoint(timestamp=p.timestamp, value=p.value, tags=p.tags) for p in points],
        count=len(points),
    )


@router.get("/health-score", response_model=HealthScoreResponse)
def get_health_score(
    window: int = Query(60, ge=1, le=10080, alias="window_minutes"),
):
    """Return a composite health score (0-100) based on latency, cache, and accuracy."""
    collector = _get_collector()
    raw = collector.get_aggregated_metrics(window)
    score, grade, factors = _compute_health_score(raw)
    return HealthScoreResponse(
        score=score,
        grade=grade,
        factors=factors,
        timestamp=_iso_now(),
    )


@router.get("/cost", response_model=CostBreakdownResponse)
def get_cost_breakdown(
    window: int = Query(60, ge=1, le=10080, alias="window_minutes"),
):
    """Return LLM cost breakdown by model for the given time window."""
    collector = _get_collector()
    by_model = collector.get_cost_breakdown(window)
    total = sum(by_model.values())
    return CostBreakdownResponse(
        window_minutes=window,
        total_cost_usd=round(total, 6),
        by_model={k: round(v, 6) for k, v in sorted(by_model.items(), key=lambda x: -x[1])},
        timestamp=_iso_now(),
    )


@router.get("/quality", response_model=QualityMetricsResponse)
def get_quality_metrics(
    window: int = Query(60, ge=1, le=10080, alias="window_minutes"),
):
    """Return retrieval quality metrics (NDCG, verification accuracy, cache hit rate)."""
    collector = _get_collector()
    raw = collector.get_aggregated_metrics(window)
    return QualityMetricsResponse(
        window_minutes=window,
        retrieval_ndcg=MetricAggregation(**raw.get("retrieval_ndcg", {})),
        verification_accuracy=MetricAggregation(**raw.get("verification_accuracy", {})),
        cache_hit_rate=MetricAggregation(**raw.get("cache_hit_rate", {})),
        timestamp=_iso_now(),
    )
