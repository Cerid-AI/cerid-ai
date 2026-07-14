# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Observability API — aggregated metrics, health score, cost breakdown, quality.

Provides real-time observability data for the React dashboard:

- ``GET /observability/metrics`` — aggregated stats for all metrics
- ``GET /observability/metrics/{name}`` — raw time series for a specific metric
- ``GET /observability/health-score`` — composite health score (0-100)
- ``GET /observability/cost`` — LLM cost breakdown by model
- ``GET /observability/quality`` — retrieval quality metrics
- ``GET /observability/verification-rates`` — timeout/uncertain claim rates
  (today + trailing 7d), Phase 0.4a
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow_iso


# --- Response models (generated: single-return dict-literal routes) ---
class GetRestartInfoResponse(BaseModel):
    process_start_iso: Any
    uptime_seconds: Any
    restart_count: Any
    last_restart_iso: Any
    timestamp: Any


class GetKnowledgeStatsHistoryResponse(BaseModel):
    days: Any
    snapshots: Any


class GetClaimAccuracyResponse(BaseModel):
    window_minutes: Any
    overall_accuracy: Any
    by_type: dict
    note: str
    sample_count: Any
    timestamp: Any


class GetCostPerQueryResponse(BaseModel):
    window_minutes: Any
    cost_per_query_usd: Any
    total_cost_usd: Any
    total_queries: Any
    timestamp: Any


class GetVerificationRatesResponse(BaseModel):
    today: dict
    last_7d: dict
    timestamp: Any



logger = logging.getLogger("ai-companion.observability")

# Process-start markers — captured at module import (which happens once
# per process boot). Surface them via /observability/restarts so trading-
# agent and other consumers can detect MCP restarts (Workstream A Phase
# 1.3 defence-in-depth). No Redis dependency for the in-process signal;
# the Redis-backed monotonic counter below is a nice-to-have when Redis
# is reachable.
_PROCESS_START_MONOTONIC = time.monotonic()
_PROCESS_START_ISO = utcnow_iso()
_RESTART_COUNTER_KEY = "cerid:mcp:restart_count"
_LAST_RESTART_ISO_KEY = "cerid:mcp:last_restart_iso"


def increment_restart_counter() -> int | None:
    """Bump the persistent restart counter on app boot.

    Best-effort. Called from the FastAPI lifespan during startup so the
    counter monotonically increases across container restarts. Returns
    the post-increment value or None if Redis is unreachable.
    """
    try:
        from app.deps import get_redis
        redis = get_redis()
        new_value = int(redis.incr(_RESTART_COUNTER_KEY))
        redis.set(_LAST_RESTART_ISO_KEY, _PROCESS_START_ISO)
        logger.info("MCP restart counter bumped to %d", new_value)
        return new_value
    except Exception as exc:
        log_swallowed_error("observability.increment_restart_counter", exc)
        return None


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


class RagasMetricsResponse(BaseModel):
    window_minutes: int
    faithfulness: MetricAggregation
    answer_relevancy: MetricAggregation
    context_precision: MetricAggregation
    context_recall: MetricAggregation
    timestamp: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    from core.utils.time import utcnow_iso
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
        scores.append(0)  # no data — don't contribute to score

    # Cache hit rate factor (weight: 20%)
    cache = metrics.get("cache_hit_rate", {})
    cache_avg = cache.get("avg")
    if cache_avg is not None:
        cache_score = min(100, cache_avg * 100)
        factors["cache"] = {"hit_rate": round(cache_avg, 3), "score": round(cache_score, 1), "weight": 20}
        scores.append(cache_score * 0.20)
    else:
        factors["cache"] = {"hit_rate": None, "score": None, "weight": 20, "status": "no_data"}
        scores.append(0)  # no data — don't contribute to score

    # Verification accuracy factor (weight: 30%)
    verif = metrics.get("verification_accuracy", {})
    verif_avg = verif.get("avg")
    if verif_avg is not None:
        verif_score = min(100, verif_avg * 100)
        factors["verification"] = {"accuracy": round(verif_avg, 3), "score": round(verif_score, 1), "weight": 30}
        scores.append(verif_score * 0.30)
    else:
        factors["verification"] = {"accuracy": None, "score": None, "weight": 30, "status": "no_data"}
        scores.append(0)  # no data — don't contribute to score

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
        scores.append(0)  # no data — don't contribute to score

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


@router.get("/ragas", response_model=RagasMetricsResponse)
def get_ragas_metrics(
    window: int = Query(60, ge=1, le=10080, alias="window_minutes"),
):
    """Return aggregated RAGAS metrics (faithfulness, relevancy, precision, recall)."""
    collector = _get_collector()
    raw = collector.get_aggregated_metrics(window)
    return RagasMetricsResponse(
        window_minutes=window,
        faithfulness=MetricAggregation(**raw.get("ragas_faithfulness", {})),
        answer_relevancy=MetricAggregation(**raw.get("ragas_answer_relevancy", {})),
        context_precision=MetricAggregation(**raw.get("ragas_context_precision", {})),
        context_recall=MetricAggregation(**raw.get("ragas_context_recall", {})),
        timestamp=_iso_now(),
    )


@router.get("/cost-per-query", response_model=GetCostPerQueryResponse)
def get_cost_per_query(
    window: int = Query(60, ge=1, le=10080, alias="window_minutes"),
):
    """Return average cost per query over the time window."""
    collector = _get_collector()
    raw = collector.get_aggregated_metrics(window)
    cost_data = raw.get("llm_cost_usd", {})
    throughput_data = raw.get("queries_per_minute", {})
    total_cost = (cost_data.get("avg", 0) or 0) * (cost_data.get("count", 0) or 0)
    query_count = throughput_data.get("count", 0) or 1
    return {
        "window_minutes": window,
        "cost_per_query_usd": round(total_cost / max(query_count, 1), 6),
        "total_cost_usd": round(total_cost, 6),
        "total_queries": query_count,
        "timestamp": _iso_now(),
    }


@router.get("/queue-depth")  # response-model-allowed: dynamic response (shape varies)
async def queue_depth_endpoint():
    """Return in-use and waiting counts for each concurrency pool.

    Surfaces the path-partitioned pools defined in ``app.concurrency``
    so dashboards can spot queue buildup on the KB pool (the usual
    suspect when chat latency spikes) before users notice.
    """
    from app.concurrency import queue_depths
    return queue_depths()


@router.get("/claim-accuracy", response_model=GetClaimAccuracyResponse)
def get_claim_accuracy(
    window: int = Query(60, ge=1, le=10080, alias="window_minutes"),
):
    """Return claim verification accuracy breakdown by claim type."""
    collector = _get_collector()
    raw = collector.get_aggregated_metrics(window)
    verif = raw.get("verification_accuracy", {})
    # Per-type breakdown not yet available from MetricsCollector;
    # overall avg used as placeholder until per-claim-type metrics are recorded.
    avg = verif.get("avg")
    return {
        "window_minutes": window,
        "overall_accuracy": avg,
        "by_type": {
            "citation": avg,
            "evasion": avg,
            "recency": avg,
            "ignorance": avg,
        },
        "note": "Per-type breakdown pending — currently shows overall average for each type",
        "sample_count": verif.get("count", 0),
        "timestamp": _iso_now(),
    }


@router.get("/verification-rates", response_model=GetVerificationRatesResponse)
def get_verification_rates_endpoint():
    """Return today's and trailing-7-day verification timeout/uncertain rates.

    Phase 0.4a: the verification pipeline had no timeout-rate or
    uncertain-rate telemetry despite ``core.agents.hallucination.verification``
    citing a 26% uncertain rate with no regression guard. Fed by
    ``app.observability.verification_metrics.record_verification_report``,
    called from ``save_verification_report`` after every persisted report.
    """
    from app.observability.verification_metrics import get_verification_rates
    rates = get_verification_rates()
    return {
        "today": rates["today"],
        "last_7d": rates["last_7d"],
        "timestamp": _iso_now(),
    }


@router.get("/claim-accuracy/{domain}", response_model=dict[str, Any])
async def get_claim_accuracy_by_domain(
    domain: str,
    window_hours: int = Query(168, ge=1, le=8760, description="Look-back window in hours (default 7 days)"),
) -> dict:
    """Return rolling user-agreement stats for verified claims in a domain.

    **Operator-facing only.** This endpoint is not surfaced to end-users.
    It feeds TrustScore component #6 (user agreement) and operator
    dashboards.

    ``domain`` may be any string matching ``Claim.domain`` in the graph.
    Pass ``"all"`` to get global stats across all domains.

    Phase R.1 of the v0.92 plan.
    """
    try:
        from app.services.feedback import get_claim_accuracy
        stats = await get_claim_accuracy(
            domain=None if domain == "all" else domain,
            window_hours=window_hours,
        )
        return stats.model_dump()
    except Exception as exc:
        log_swallowed_error("observability.get_claim_accuracy_by_domain", exc)
        return {
            "total_rated": 0,
            "positive": 0,
            "negative": 0,
            "neutral": 0,
            "agreement_rate": 0.0,
            "domain": None if domain == "all" else domain,
            "window_hours": window_hours,
            "as_of_iso": _iso_now(),
            "error": "Neo4j unavailable",
        }


@router.get("/restarts", response_model=GetRestartInfoResponse)
async def get_restart_info() -> dict:
    """Process-restart visibility (Workstream A Phase 1.3).

    Exposes:
      - ``process_start_iso`` and ``uptime_seconds`` — always available
        from in-process state; useful for "did MCP restart since the
        last poll?" detection.
      - ``restart_count`` — monotonic counter incremented at app boot
        and persisted in Redis. ``None`` when Redis is unreachable.
      - ``last_restart_iso`` — mirror of ``process_start_iso`` written
        to Redis on the same boot. ``None`` when Redis is unreachable.

    Trading-agent and other dependents can poll this endpoint to align
    their circuit-breakers with real MCP restart events.
    """
    uptime_s = round(time.monotonic() - _PROCESS_START_MONOTONIC, 2)
    counter: int | None = None
    last_restart: str | None = None
    try:
        from app.deps import get_redis
        redis = get_redis()
        raw_counter = redis.get(_RESTART_COUNTER_KEY)
        if raw_counter is not None:
            counter = int(raw_counter)
        raw_last = redis.get(_LAST_RESTART_ISO_KEY)
        if raw_last is not None:
            last_restart = raw_last.decode() if isinstance(raw_last, bytes) else str(raw_last)
    except Exception as exc:
        log_swallowed_error("observability.get_restart_info", exc)
    return {
        "process_start_iso": _PROCESS_START_ISO,
        "uptime_seconds": uptime_s,
        "restart_count": counter,
        "last_restart_iso": last_restart,
        "timestamp": _iso_now(),
    }


# ---------------------------------------------------------------------------
# Phase R.2 — Community explorer endpoints
# ---------------------------------------------------------------------------


@router.get("/communities", response_model=list[Any])
async def list_communities_endpoint(
    min_size: int = Query(3, ge=1, description="Minimum community member count"),
    limit: int = Query(30, ge=1, le=200, description="Max results"),
    level: int = Query(0, ge=0, description="Leiden hierarchy depth (0 = finest)"),
) -> list[dict]:
    """List Leiden communities that have cached LLM summaries.

    Returns communities ordered by member_count descending.  Only
    communities at ``level`` (default 0) with ≥ ``min_size`` members
    and an existing cached summary are returned.

    Phase R.2 of the v0.92 plan.
    """
    from app.deps import get_neo4j
    from app.services.community_pages import list_top_communities

    try:
        driver = get_neo4j()
    except Exception as exc:
        log_swallowed_error("observability.list_communities.neo4j", exc)
        raise HTTPException(status_code=503, detail="Neo4j unavailable") from exc

    communities = await list_top_communities(
        driver, min_size=min_size, limit=limit, level=level
    )
    return [c.model_dump() for c in communities]


@router.get("/communities/{community_id:path}", response_model=dict[str, Any])
async def get_community_endpoint(community_id: str) -> dict:
    """Return the full community record for ``community_id``.

    ``community_id`` follows the pattern ``"{level}:{native_id}"``
    (e.g. ``"0:42"``).  The colon is URL-encoded in requests; the
    ``:path`` converter captures the slash-safe form.

    Returns 404 when no community with that id exists.

    Phase R.2 of the v0.92 plan.
    """
    from app.deps import get_neo4j
    from app.services.community_pages import get_community_page

    try:
        driver = get_neo4j()
    except Exception as exc:
        log_swallowed_error("observability.get_community.neo4j", exc)
        raise HTTPException(status_code=503, detail="Neo4j unavailable") from exc

    community = await get_community_page(driver, community_id)
    if community is None:
        raise HTTPException(
            status_code=404,
            detail=f"Community not found: {community_id}",
        )
    return community.model_dump()


@router.get("/trust-score", response_model=dict[str, Any])
async def get_trust_score() -> dict:
    """System evaluation posture, 0–100, with disclosed component scores.

    See ``docs/PRODUCT_STORY.md`` and ``docs/EVAL_BASELINES.md``. The
    score is a straight mean of normalized component values — no learned
    weights. Components with ``status='not_available'`` are excluded
    from the mean. This endpoint is **pure presentation**; it does not
    affect retrieval, generation, or any model decision.

    Phase E.5 of the v0.92 plan. Preservation gate I14.
    """
    from app.services.trust_score import compute_trust_score
    driver = None
    try:
        from app.deps import get_neo4j
        driver = get_neo4j()
    except Exception as exc:
        log_swallowed_error("observability.get_trust_score.neo4j", exc)
    ts = compute_trust_score(neo4j_driver=driver)
    return ts.model_dump()


# ---------------------------------------------------------------------------
# Ingestion Experience surfaces — Phase 1 of the 2026-05-24 plan
# ---------------------------------------------------------------------------

_KNOWLEDGE_STATS_CACHE_KEY = "cerid:knowledge_stats:cached"
_KNOWLEDGE_STATS_CACHE_TTL_S = 60


@router.get("/knowledge-stats")  # response-model-allowed: dynamic response (shape varies)
async def get_knowledge_stats() -> dict:
    """Corpus-growth snapshot — powers the Sources pane F9 hero card.

    Five orthogonal dimensions (nodes / edges / chunks / diversity /
    growth) returned in a single payload. Redis-cached for 60s so a
    busy Sources pane refresh doesn't hammer Neo4j. SSE artifact-
    arrival events invalidate the cache (Phase 2 wiring).
    """
    import json as _json

    from app.db.neo4j.stats import fetch_current_stats

    # Try Redis cache first.
    try:
        from app.deps import get_redis
        redis_client = get_redis()
        if redis_client is not None:
            cached = redis_client.get(_KNOWLEDGE_STATS_CACHE_KEY)
            if cached is not None:
                if isinstance(cached, bytes):
                    cached = cached.decode("utf-8")
                return _json.loads(cached)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("observability.knowledge_stats.cache_read", exc)

    # Cache miss — compute fresh.
    try:
        from app.deps import get_neo4j
        driver = get_neo4j()
        snapshot = fetch_current_stats(driver)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("observability.knowledge_stats.compute", exc)
        return _empty_knowledge_stats()

    # Warm the cache for the next 60s.
    try:
        if redis_client is not None:
            redis_client.setex(
                _KNOWLEDGE_STATS_CACHE_KEY,
                _KNOWLEDGE_STATS_CACHE_TTL_S,
                _json.dumps(snapshot, default=str),
            )
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("observability.knowledge_stats.cache_warm", exc)

    return snapshot


@router.get("/knowledge-stats/history", response_model=GetKnowledgeStatsHistoryResponse)
async def get_knowledge_stats_history(
    days: int = Query(default=30, ge=1, le=365),
) -> dict:
    """Daily corpus snapshots for sparkline rendering — powers F9
    sparklines under each metric. Returns up to ``days`` of daily
    snapshots, oldest first. Daily granularity is good enough for a
    60×16 px sparkline — finer resolution would add no signal."""
    from app.db.neo4j.stats import fetch_stats_history

    try:
        from app.deps import get_neo4j
        driver = get_neo4j()
        snapshots = fetch_stats_history(driver, days=days)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("observability.knowledge_stats.history", exc)
        snapshots = []

    return {"days": days, "snapshots": snapshots}


@router.get("/source-activity")
async def source_activity_stream(
    source_id: str | None = Query(default=None, description="Filter to one source"),
) -> StreamingResponse:
    """SSE stream of source ingestion events — powers the Sources
    pane Activity tab and the Constellation particle stream.

    Each event line is a JSON :class:`core.ingest.sources.base.SourceArtifactEvent`
    payload. The stream stays open indefinitely; clients reconnect
    via the standard EventSource auto-retry on disconnect.

    Pass ``?source_id=<uuid>`` to scope to a single source (used by
    the source-detail pane's per-source activity feed).

    Phase 1 ships the endpoint shape. The actual artifact-arrival
    subscriber wires up in Phase 2 when the connector implementations
    land — until then this emits keepalive comments so the FE can
    render the connection-established state.
    """
    import asyncio
    import json as _json

    async def event_generator():
        # Initial connected event so the FE can confirm subscription.
        yield (
            f"data: {_json.dumps({'type': 'connected', 'source_id': source_id})}\n\n"
        ).encode("utf-8")

        # Phase 1 placeholder loop — emits a keepalive every 15s.
        # Phase 2 swaps in the real subscriber (Redis pubsub from the
        # ingestion service publishes artifact-arrival events).
        try:
            while True:
                await asyncio.sleep(15)
                yield b": keepalive\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _empty_knowledge_stats() -> dict:
    """Fallback when Neo4j is unreachable — shape-stable zero snapshot."""
    return {
        "nodes": {"artifacts": 0, "entities": 0, "memories": 0, "sources": 0},
        "edges": {
            "mentions": 0, "relates_to": 0, "wikilinks": 0,
            "from_source": 0, "has_contradiction": 0,
        },
        "chunks": 0,
        "diversity": {"source_kinds": 0, "domains": 0},
        "growth": {
            "artifacts_24h": 0, "artifacts_7d": 0,
            "first_artifact_at": None, "corpus_age_days": 0,
        },
        "captured_at": utcnow_iso(),
    }
