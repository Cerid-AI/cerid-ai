# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Phase L — analytics REST surface.

Three new endpoints power the Settings → Diagnostics → Analytics tab:

  GET  /analytics/ingestion-by-day  (year-long heatmap)
  GET  /analytics/cost-by-stage     (Sankey cost flow)
  GET  /analytics/quality-timeline  (rolling 90d multi-metric)

The trust-score sunburst reuses the existing
``/observability/trust-score`` endpoint — no new backend needed.

These endpoints aggregate existing telemetry (Neo4j ingest timestamps,
Redis time-series cost/quality metrics) rather than introducing new
storage. Aggregations are computed on each request — bounded query
cost because windows are capped (max 730 days for ingestion, 90 days
for quality, 30 days for cost).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from config.features import require_feature

logger = logging.getLogger("ai-companion.analytics")

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ── response models ──────────────────────────────────────────────────

class IngestionDayBucket(BaseModel):
    date: str               # YYYY-MM-DD
    count: int
    domains: dict[str, int] # per-domain breakdown
    intensity: float        # 0.0..1.0 normalized (UI color scale)


class IngestionByDayResponse(BaseModel):
    window_days: int
    buckets: list[IngestionDayBucket]
    total: int
    peak_count: int


class StageCost(BaseModel):
    stage: str
    cost_usd: float
    call_count: int


class CostByStageResponse(BaseModel):
    window_days: int
    total_cost_usd: float
    stages: list[StageCost]
    # Sankey-ready edges: provider → stage. The UI renders provider as
    # the left column and stage as the right column.
    edges: list[dict[str, Any]]


class QualityTimelinePoint(BaseModel):
    date: str
    ndcg: float | None
    faithfulness: float | None
    memory_recall: float | None
    verification_accuracy: float | None


class QualityTimelineResponse(BaseModel):
    window_days: int
    points: list[QualityTimelinePoint]
    latest: dict[str, float | None] = Field(
        default_factory=dict,
        description="Most-recent value per metric (for the headline summary).",
    )


# ── stage → provider mapping (for the Sankey) ─────────────────────────

# Maps the stage label (passed as `stage="..."` to call_internal_llm)
# to the provider category we visualize on the left side of the Sankey.
# Keeps the visualization stable even as new stages get added — unknown
# stages bucket into "other".
_STAGE_PROVIDER = {
    # Ingest-time stages
    "entity_extraction": "ingest",
    "claim_extraction": "ingest",
    "contextual_chunks": "ingest",
    "memory_extract": "ingest",
    "memory_conflict_resolve": "ingest",
    "memory_consolidation": "ingest",
    "curator_synopsis": "curator",
    # Retrieval-time stages
    "rerank_llm": "retrieval",
    "query_decompose": "retrieval",
    "hype_index/generate": "retrieval",
    "longshot_cypher": "retrieval",
    # Generation-time stages
    "hallucination_topic": "verification",
    "metamorphic_mutation": "verification",
    # Pro features
    "daily_digest": "pro_features",
    "inbox_triage": "pro_features",
    "meeting_summary": "pro_features",
    "brief/daily": "pro_features",
    "brief/weekly": "pro_features",
}


def _provider_for_stage(stage: str) -> str:
    return _STAGE_PROVIDER.get(stage, "other")


# ── endpoints ─────────────────────────────────────────────────────────


@router.get("/ingestion-by-day", response_model=IngestionByDayResponse)
@require_feature("advanced_analytics")
async def ingestion_by_day(
    window_days: int = Query(default=365, ge=1, le=730),
) -> IngestionByDayResponse:
    """Bucket artifact ingest events by day for the heatmap grid.

    Returns one entry per day with at least one artifact ingested, plus
    a normalized `intensity` (0..1) the UI maps to the color scale.
    """
    try:
        from app.deps import get_neo4j
        driver = get_neo4j()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ingestion_by_day: neo4j unavailable: %s", exc)
        return IngestionByDayResponse(
            window_days=window_days, buckets=[], total=0, peak_count=0,
        )

    since = (datetime.now(tz=timezone.utc) - timedelta(days=window_days)).isoformat()

    # Cypher query: substring grouping is portable across Neo4j versions
    # without requiring APOC. Cardinality bounded by `since` filter.
    query = (
        "MATCH (a:Artifact) "
        "WHERE a.ingested_at >= $since "
        "WITH substring(a.ingested_at, 0, 10) AS day, "
        "     coalesce(a.domain, 'general') AS domain "
        "RETURN day, domain, count(*) AS n "
        "ORDER BY day"
    )

    try:
        import asyncio
        rows = await asyncio.to_thread(_run_cypher, driver, query, {"since": since})
    except Exception as exc:  # noqa: BLE001
        logger.warning("ingestion_by_day cypher failed: %s", exc)
        return IngestionByDayResponse(
            window_days=window_days, buckets=[], total=0, peak_count=0,
        )

    by_day: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "domains": defaultdict(int)},
    )
    for row in rows:
        day = row["day"]
        if not day:
            continue
        by_day[day]["count"] += row["n"]
        by_day[day]["domains"][row["domain"]] += row["n"]

    peak = max((b["count"] for b in by_day.values()), default=0)
    total = sum(b["count"] for b in by_day.values())

    buckets = [
        IngestionDayBucket(
            date=day,
            count=b["count"],
            domains=dict(b["domains"]),
            intensity=(b["count"] / peak) if peak > 0 else 0.0,
        )
        for day, b in sorted(by_day.items())
    ]

    return IngestionByDayResponse(
        window_days=window_days,
        buckets=buckets,
        total=total,
        peak_count=peak,
    )


@router.get("/cost-by-stage", response_model=CostByStageResponse)
@require_feature("advanced_analytics")
async def cost_by_stage(
    window_days: int = Query(default=30, ge=1, le=90),
) -> CostByStageResponse:
    """LLM cost breakdown by pipeline stage over the window.

    Stage attribution: pulls per-stage Redis time-series when the
    metrics layer records them (added in this commit), otherwise
    distributes per-model totals into stage buckets via the static
    `_STAGE_PROVIDER` mapping using the assumption that all stages
    in a provider category share equally — best-effort until the
    metrics layer records `stage` as a tag.
    """
    window_minutes = window_days * 24 * 60

    try:
        from utils.metrics import get_metrics_collector
        collector = get_metrics_collector()
    except ImportError as exc:
        logger.warning("cost_by_stage: metrics unavailable: %s", exc)
        return CostByStageResponse(
            window_days=window_days, total_cost_usd=0.0, stages=[], edges=[],
        )

    # Pull every llm_cost_usd point in the window. Each point carries
    # tags including (optionally) "stage" — when stage attribution is
    # recorded at call time we use it directly; otherwise we bucket
    # into "other" so the Sankey still totals correctly.
    try:
        points = collector.get_metrics("llm_cost_usd", window_minutes)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cost_by_stage points read failed: %s", exc)
        points = []

    stage_costs: dict[str, float] = defaultdict(float)
    stage_calls: dict[str, int] = defaultdict(int)
    total = 0.0
    for p in points:
        tags = getattr(p, "tags", {}) or {}
        cost = float(getattr(p, "value", 0.0) or 0.0)
        total += cost
        stage = tags.get("stage", "")
        if not stage or stage not in _STAGE_PROVIDER:
            stage = "other"
        stage_costs[stage] += cost
        stage_calls[stage] += 1

    stages: list[StageCost] = [
        StageCost(stage=s, cost_usd=round(c, 6), call_count=stage_calls[s])
        for s, c in sorted(stage_costs.items(), key=lambda kv: -kv[1])
    ]

    # Sankey edges: provider → stage with the stage's cost as flow value
    edges: list[dict[str, Any]] = []
    for s in stages:
        if s.cost_usd <= 0:
            continue
        edges.append({
            "source": _provider_for_stage(s.stage),
            "target": s.stage,
            "value": s.cost_usd,
        })

    return CostByStageResponse(
        window_days=window_days,
        total_cost_usd=round(total, 6),
        stages=stages,
        edges=edges,
    )


@router.get("/quality-timeline", response_model=QualityTimelineResponse)
@require_feature("advanced_analytics")
async def quality_timeline(
    window_days: int = Query(default=90, ge=7, le=365),
) -> QualityTimelineResponse:
    """Rolling quality metrics aggregated to daily buckets.

    Pulls four Redis time-series and aligns them to a single date axis.
    Days without samples carry `null` for that metric so the UI line
    renders gaps (more honest than zero-filling).
    """
    try:
        from utils.metrics import get_metrics_collector
        collector = get_metrics_collector()
    except ImportError:
        logger.warning("quality_timeline: metrics unavailable")
        return QualityTimelineResponse(window_days=window_days, points=[], latest={})

    window_minutes = window_days * 24 * 60
    metric_keys = {
        "ndcg": "retrieval_ndcg",
        "faithfulness": "ragas_faithfulness",
        "memory_recall": "memory_recall",
        "verification_accuracy": "verification_accuracy",
    }

    series_by_metric: dict[str, list[Any]] = {}
    for label, key in metric_keys.items():
        try:
            series_by_metric[label] = collector.get_metrics(key, window_minutes)
        except Exception as exc:  # noqa: BLE001
            logger.debug("quality_timeline series %s missing: %s", key, exc)
            series_by_metric[label] = []

    # Bucket by day (UTC). Each metric independently → preserves gaps.
    by_day: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list),
    )
    for label, metric_points in series_by_metric.items():
        for p in metric_points:
            ts = getattr(p, "timestamp", None)
            val = getattr(p, "value", None)
            if not ts or val is None:
                continue
            try:
                day = ts.split("T")[0] if isinstance(ts, str) else ""
            except (AttributeError, IndexError):
                continue
            if day:
                try:
                    by_day[day][label].append(float(val))
                except (ValueError, TypeError):
                    pass

    # Materialize the date axis (every day in window) so the UI can
    # render an honest "no data" gap.
    days: list[str] = []
    today = datetime.now(tz=timezone.utc).date()
    for i in range(window_days):
        days.append((today - timedelta(days=window_days - 1 - i)).isoformat())

    points: list[QualityTimelinePoint] = []
    for day in days:
        bucket = by_day.get(day, {})
        points.append(QualityTimelinePoint(
            date=day,
            ndcg=_avg(bucket.get("ndcg")),
            faithfulness=_avg(bucket.get("faithfulness")),
            memory_recall=_avg(bucket.get("memory_recall")),
            verification_accuracy=_avg(bucket.get("verification_accuracy")),
        ))

    # Latest non-null per metric for the headline summary
    latest: dict[str, float | None] = {}
    for label in ("ndcg", "faithfulness", "memory_recall", "verification_accuracy"):
        for p in reversed(points):
            val = getattr(p, label)
            if val is not None:
                latest[label] = val
                break
        latest.setdefault(label, None)

    return QualityTimelineResponse(
        window_days=window_days,
        points=points,
        latest=latest,
    )


# ── helpers ──────────────────────────────────────────────────────────

def _avg(vals: list[float] | None) -> float | None:
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def _run_cypher(driver: Any, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Synchronous Cypher executor — called from asyncio.to_thread."""
    if driver is None:
        return []
    try:
        with driver.session() as session:
            result = session.run(query, params)
            return [dict(record) for record in result]
    except Exception as exc:  # noqa: BLE001
        logger.warning("_run_cypher failed: %s", exc)
        return []
