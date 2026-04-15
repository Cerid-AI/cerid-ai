# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Daily digest endpoint."""
from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Query

from app.deps import get_neo4j, get_redis
from app.routers.health import health_check
from utils.cache import get_log
from utils.time import utcnow, utcnow_iso

router = APIRouter()
logger = logging.getLogger("ai-companion")


@router.get("/digest")
async def digest_endpoint(hours: int = Query(24, ge=1, le=168)):
    """
    Summarize recent activity: new artifacts, connections, and system health.

    Args:
        hours: Lookback window (default 24h, max 7 days)
    """
    cutoff = (utcnow().replace(tzinfo=None) - timedelta(hours=hours)).isoformat()

    # Recent artifacts from Neo4j
    recent_artifacts = []
    new_relationships = 0
    try:
        driver = get_neo4j()
        with driver.session() as session:
            result = session.run(
                "MATCH (a:Artifact) WHERE a.ingested_at >= $cutoff "
                "RETURN a.id AS id, a.filename AS filename, a.domain AS domain, "
                "a.summary AS summary, a.ingested_at AS ingested_at "
                "ORDER BY a.ingested_at DESC LIMIT 50",
                cutoff=cutoff,
            )
            for record in result:
                recent_artifacts.append({
                    "id": record["id"],
                    "filename": record["filename"],
                    "domain": record["domain"],
                    "summary": (record["summary"] or "")[:100],
                    "ingested_at": record["ingested_at"],
                })

            # Count new relationships
            rel_result = session.run(
                "MATCH ()-[r]->() WHERE r.created_at >= $cutoff "
                "RETURN count(r) AS cnt",
                cutoff=cutoff,
            )
            rec = rel_result.single()
            if rec:
                new_relationships = rec["cnt"]
    except Exception as e:
        logger.warning(f"Digest Neo4j query failed: {e}")

    # Domain distribution of recent artifacts
    domain_counts: dict[str, int] = {}
    for a in recent_artifacts:
        domain_counts[a["domain"]] = domain_counts.get(a["domain"], 0) + 1

    # System health
    try:
        system_health = health_check()
    except Exception as e:
        logger.debug(f"Health check failed during digest: {e}")
        system_health = {"status": "unknown"}

    # Recent activity log from Redis — pull a wider slice so we can surface errors
    # in the window even when the most recent 20 entries are all successful.
    recent_events: list[dict] = []
    error_items: list[dict] = []
    try:
        log = get_log(get_redis(), limit=500)
        if isinstance(log, list):
            for entry in log:
                ts = entry.get("timestamp")
                if ts and ts < cutoff:
                    # Entries older than the window — log is ordered newest-first,
                    # so we can stop scanning here.
                    break
                recent_events.append(entry)
                if entry.get("event") == "error":
                    error_items.append({
                        "timestamp": ts,
                        "filename": entry.get("filename"),
                        "artifact_id": entry.get("artifact_id"),
                        "domain": entry.get("domain"),
                        "detail": entry.get("detail") or entry.get("error") or entry.get("message"),
                    })
    except Exception as e:
        logger.debug(f"Redis activity log unavailable for digest: {e}")

    return {
        "period_hours": hours,
        "generated_at": utcnow_iso(),
        "artifacts": {
            "count": len(recent_artifacts),
            "items": recent_artifacts,
            "by_domain": domain_counts,
        },
        "relationships": {
            "new_count": new_relationships,
        },
        "health": system_health,
        "recent_events": len(recent_events),
        "errors": {
            "count": len(error_items),
            "items": error_items[:50],
        },
    }
