# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Curation Agent — artifact quality scoring (Phase 14).

Scores every artifact on 4 dimensions (summary, keywords, freshness,
completeness), stores the composite quality_score on Neo4j Artifact
nodes, and returns a distribution report.  All scoring is pure math —
no LLM calls required.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, List, Optional

import config
from db.neo4j.artifacts import list_artifacts
from utils.time import utcnow, utcnow_iso

logger = logging.getLogger("ai-companion.curator")


# ---------------------------------------------------------------------------
# Individual scoring functions (pure, testable)
# ---------------------------------------------------------------------------

def score_summary(summary: str) -> float:
    """Score summary quality based on length heuristics. Returns [0, 1]."""
    if not summary or not summary.strip():
        return 0.0
    length = len(summary.strip())
    if length < config.QUALITY_SUMMARY_MIN_CHARS:
        return length / config.QUALITY_SUMMARY_MIN_CHARS
    if length <= config.QUALITY_SUMMARY_MAX_CHARS:
        return 1.0
    overshoot = length - config.QUALITY_SUMMARY_MAX_CHARS
    return max(0.3, 1.0 - (overshoot / 1000))


def score_keywords(keywords_json: str) -> float:
    """Score keyword quality based on count. Returns [0, 1]."""
    try:
        keywords = json.loads(keywords_json) if keywords_json else []
    except (json.JSONDecodeError, TypeError):
        keywords = []
    if not keywords:
        return 0.0
    count = len(keywords)
    if count >= config.QUALITY_KEYWORDS_OPTIMAL:
        return 1.0
    return count / config.QUALITY_KEYWORDS_OPTIMAL


def score_freshness(ingested_at: str, modified_at: Optional[str] = None) -> float:
    """Score freshness using exponential decay. Returns [0, 1]."""
    timestamp = modified_at or ingested_at
    if not timestamp:
        return 0.5
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(timestamp)
        now = utcnow()
        # Handle tz-aware vs tz-naive comparison
        if dt.tzinfo is None:
            now = now.replace(tzinfo=None)
        age_days = (now - dt).total_seconds() / 86400.0
        if age_days < 0:
            age_days = 0
        return math.pow(2, -age_days / config.TEMPORAL_HALF_LIFE_DAYS)
    except (ValueError, TypeError):
        return 0.5


def score_completeness(artifact: Dict[str, Any]) -> float:
    """Score metadata completeness. Returns [0, 1]."""
    checks = 0
    total = 4

    # Has a non-trivial summary?
    summary = artifact.get("summary", "")
    if summary and len(summary.strip()) >= 20:
        checks += 1

    # Has keywords?
    kw_json = artifact.get("keywords", "[]")
    try:
        kw = json.loads(kw_json) if kw_json else []
    except (json.JSONDecodeError, TypeError):
        kw = []
    if len(kw) >= 2:
        checks += 1

    # Has tags?
    tags_json = artifact.get("tags", "[]")
    try:
        tags = json.loads(tags_json) if tags_json else []
    except (json.JSONDecodeError, TypeError):
        tags = []
    if tags:
        checks += 1

    # Has non-default sub_category?
    sub_cat = artifact.get("sub_category", "")
    if sub_cat and sub_cat != config.DEFAULT_SUB_CATEGORY:
        checks += 1

    return checks / total


def compute_quality_score(artifact: Dict[str, Any]) -> Dict[str, Any]:
    """Compute weighted quality score for a single artifact."""
    s_summary = score_summary(artifact.get("summary", ""))
    s_keywords = score_keywords(artifact.get("keywords", "[]"))
    s_freshness = score_freshness(
        artifact.get("ingested_at", ""),
        artifact.get("modified_at"),
    )
    s_completeness = score_completeness(artifact)

    total = (
        config.QUALITY_WEIGHT_SUMMARY * s_summary
        + config.QUALITY_WEIGHT_KEYWORDS * s_keywords
        + config.QUALITY_WEIGHT_FRESHNESS * s_freshness
        + config.QUALITY_WEIGHT_COMPLETENESS * s_completeness
    )

    issues: List[str] = []
    if s_summary < 0.5:
        issues.append("summary_weak" if s_summary > 0 else "summary_missing")
    if s_keywords < 0.5:
        issues.append("keywords_sparse" if s_keywords > 0 else "keywords_missing")
    if s_completeness < 0.5:
        issues.append("metadata_incomplete")
    if s_freshness < 0.3:
        issues.append("stale")

    return {
        "quality_score": round(total, 4),
        "breakdown": {
            "summary": round(s_summary, 4),
            "keywords": round(s_keywords, 4),
            "freshness": round(s_freshness, 4),
            "completeness": round(s_completeness, 4),
        },
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Score distribution helper
# ---------------------------------------------------------------------------

def _score_distribution(scores: List[float]) -> Dict[str, int]:
    """Categorize scores into quality tiers."""
    dist = {"excellent": 0, "good": 0, "fair": 0, "poor": 0}
    for s in scores:
        if s >= 0.8:
            dist["excellent"] += 1
        elif s >= 0.6:
            dist["good"] += 1
        elif s >= 0.4:
            dist["fair"] += 1
        else:
            dist["poor"] += 1
    return dist


# ---------------------------------------------------------------------------
# Neo4j quality score persistence
# ---------------------------------------------------------------------------

def _store_quality_scores(
    neo4j_driver,
    scores: List[Dict[str, Any]],
) -> int:
    """Batch-update quality_score on Artifact nodes. Returns count updated."""
    if not scores:
        return 0
    now = utcnow_iso()
    with neo4j_driver.session() as session:
        result = session.run(
            """
            UNWIND $items AS item
            MATCH (a:Artifact {id: item.id})
            SET a.quality_score = item.score,
                a.quality_scored_at = $now
            RETURN count(a) AS updated
            """,
            items=[{"id": s["artifact_id"], "score": s["quality_score"]} for s in scores],
            now=now,
        )
        record = result.single()
        return record["updated"] if record else 0


# ---------------------------------------------------------------------------
# Main curation function
# ---------------------------------------------------------------------------

async def curate(
    neo4j_driver,
    mode: str = "audit",
    domains: Optional[List[str]] = None,
    max_artifacts: int = 200,
) -> Dict[str, Any]:
    """Score artifact quality across the knowledge base.

    Args:
        neo4j_driver: Neo4j driver instance.
        mode: "audit" (score and report, store scores).
        domains: Filter to specific domains (None = all).
        max_artifacts: Max artifacts to score per domain.
    """
    target_domains = domains or config.DOMAINS

    all_scores: List[Dict[str, Any]] = []

    for domain in target_domains:
        try:
            artifacts = list_artifacts(neo4j_driver, domain=domain, limit=max_artifacts)
        except Exception as e:
            logger.warning(f"Failed to list artifacts for {domain}: {e}")
            continue

        for artifact in artifacts:
            report = compute_quality_score(artifact)
            all_scores.append({
                "artifact_id": artifact["id"],
                "filename": artifact["filename"],
                "domain": domain,
                "quality_score": report["quality_score"],
                "breakdown": report["breakdown"],
                "issues": report["issues"],
            })

    # Store scores in Neo4j
    updated = 0
    if all_scores:
        try:
            updated = _store_quality_scores(neo4j_driver, all_scores)
        except Exception as e:
            logger.error(f"Failed to store quality scores: {e}")

    score_values = [s["quality_score"] for s in all_scores]
    avg_score = sum(score_values) / len(score_values) if score_values else 0.0

    # Low-quality artifacts for review
    low_quality = sorted(
        [s for s in all_scores if s["quality_score"] < 0.5],
        key=lambda x: x["quality_score"],
    )

    return {
        "timestamp": utcnow_iso(),
        "mode": mode,
        "artifacts_scored": len(all_scores),
        "artifacts_stored": updated,
        "avg_quality_score": round(avg_score, 4),
        "score_distribution": _score_distribution(score_values),
        "domains_scored": target_domains,
        "low_quality_artifacts": low_quality[:20],
    }
