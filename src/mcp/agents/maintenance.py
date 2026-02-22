"""
Maintenance Agent - Scheduled health checks and automated cleanup.

Provides:
- Comprehensive system health check (all DBs + Bifrost connectivity)
- Stale artifact detection and optional purge
- Collection size monitoring and compaction recommendations
- Orphan cleanup orchestration (wraps rectify agent)
- Maintenance run summary with before/after metrics
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import chromadb
import httpx

import config
from utils.cache import log_event

logger = logging.getLogger("ai-companion.maintenance")


# ---------------------------------------------------------------------------
# System health
# ---------------------------------------------------------------------------

def check_system_health(
    neo4j_driver,
    chroma_client: chromadb.HttpClient,
    redis_client,
) -> Dict[str, Any]:
    """
    Comprehensive health check across all services.

    Goes beyond the basic /health endpoint — checks data integrity,
    collection counts, and Bifrost reachability.
    """
    health = {
        "timestamp": datetime.utcnow().isoformat(),
        "services": {},
        "data": {},
    }

    # ChromaDB
    try:
        chroma_client.heartbeat()
        collections = chroma_client.list_collections()
        total_chunks = 0
        collection_sizes = {}
        for col in collections:
            count = col.count()
            collection_sizes[col.name] = count
            total_chunks += count
        health["services"]["chromadb"] = "connected"
        health["data"]["collections"] = len(collections)
        health["data"]["total_chunks"] = total_chunks
        health["data"]["collection_sizes"] = collection_sizes
    except Exception as e:
        health["services"]["chromadb"] = f"error: {e}"

    # Neo4j
    try:
        with neo4j_driver.session() as session:
            result = session.run(
                "MATCH (a:Artifact) RETURN count(a) AS artifact_count"
            )
            record = result.single()
            artifact_count = record["artifact_count"] if record else 0

            result = session.run(
                "MATCH (d:Domain) RETURN count(d) AS domain_count"
            )
            record = result.single()
            domain_count = record["domain_count"] if record else 0

        health["services"]["neo4j"] = "connected"
        health["data"]["artifacts"] = artifact_count
        health["data"]["domains"] = domain_count
    except Exception as e:
        health["services"]["neo4j"] = f"error: {e}"

    # Redis
    try:
        redis_client.ping()
        log_size = redis_client.llen(config.REDIS_INGEST_LOG)
        health["services"]["redis"] = "connected"
        health["data"]["audit_log_entries"] = log_size
    except Exception as e:
        health["services"]["redis"] = f"error: {e}"

    # Bifrost (LLM Gateway)
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Can't run sync check inside async — mark as skipped
            health["services"]["bifrost"] = "skipped (async context)"
        else:
            health["services"]["bifrost"] = _check_bifrost_sync()
    except Exception:
        health["services"]["bifrost"] = "skipped"

    # Overall status
    service_statuses = health["services"]
    all_ok = all(
        v == "connected" or v.startswith("skipped")
        for v in service_statuses.values()
    )
    health["overall"] = "healthy" if all_ok else "degraded"

    return health


def _check_bifrost_sync() -> str:
    """Synchronous Bifrost health check."""
    import urllib.request
    try:
        url = config.BIFROST_URL.replace("/v1", "/health")
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return "connected"
            return f"status {resp.status}"
    except Exception as e:
        return f"unreachable: {e}"


async def check_bifrost_health() -> str:
    """Async Bifrost health check."""
    try:
        url = config.BIFROST_URL.replace("/v1", "/health")
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return "connected"
            return f"status {resp.status_code}"
    except Exception as e:
        return f"unreachable: {e}"


# ---------------------------------------------------------------------------
# Stale artifact management
# ---------------------------------------------------------------------------

def find_stale_artifacts(
    neo4j_driver,
    days_threshold: int = 90,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Find artifacts older than the threshold."""
    cutoff = (datetime.utcnow() - timedelta(days=days_threshold)).isoformat()

    with neo4j_driver.session() as session:
        result = session.run(
            """
            MATCH (a:Artifact)-[:BELONGS_TO]->(d:Domain)
            WHERE a.ingested_at < $cutoff
            AND (a.recategorized_at IS NULL OR a.recategorized_at < $cutoff)
            RETURN a.id AS id, a.filename AS filename, d.name AS domain,
                   a.ingested_at AS ingested_at, a.chunk_count AS chunk_count,
                   a.chunk_ids AS chunk_ids
            ORDER BY a.ingested_at ASC
            LIMIT $limit
            """,
            cutoff=cutoff,
            limit=limit,
        )
        return [dict(record) for record in result]


def purge_artifacts(
    neo4j_driver,
    chroma_client: chromadb.HttpClient,
    artifact_ids: List[str],
    redis_client=None,
) -> Dict[str, Any]:
    """
    Remove specified artifacts from Neo4j and ChromaDB.

    Args:
        artifact_ids: List of artifact UUIDs to purge
        redis_client: Optional Redis client for audit logging

    Returns:
        Summary of purged artifacts
    """
    purged = []
    errors = []

    for artifact_id in artifact_ids:
        try:
            # Get artifact info from Neo4j
            with neo4j_driver.session() as session:
                result = session.run(
                    """
                    MATCH (a:Artifact {id: $id})-[:BELONGS_TO]->(d:Domain)
                    RETURN a.id AS id, a.filename AS filename, d.name AS domain,
                           a.chunk_ids AS chunk_ids
                    """,
                    id=artifact_id,
                )
                record = result.single()
                if not record:
                    errors.append({"id": artifact_id, "error": "not found"})
                    continue

            domain = record["domain"]
            filename = record["filename"]
            chunk_ids = json.loads(record.get("chunk_ids") or "[]")

            # Remove chunks from ChromaDB
            if chunk_ids:
                collection_name = f"domain_{domain}"
                try:
                    collection = chroma_client.get_collection(name=collection_name)
                    collection.delete(ids=chunk_ids)
                except Exception as e:
                    logger.warning(f"Failed to delete chunks for {artifact_id}: {e}")

            # Remove from Neo4j
            with neo4j_driver.session() as session:
                session.run(
                    "MATCH (a:Artifact {id: $id}) DETACH DELETE a",
                    id=artifact_id,
                )

            purged.append({
                "id": artifact_id,
                "filename": filename,
                "domain": domain,
                "chunks_removed": len(chunk_ids),
            })

            # Audit log
            if redis_client:
                try:
                    log_event(
                        redis_client,
                        event_type="maintenance_purge",
                        artifact_id=artifact_id,
                        domain=domain,
                        filename=filename,
                    )
                except Exception:
                    pass

        except Exception as e:
            errors.append({"id": artifact_id, "error": str(e)})

    return {
        "purged": purged,
        "purged_count": len(purged),
        "errors": errors,
        "error_count": len(errors),
    }


# ---------------------------------------------------------------------------
# Collection maintenance
# ---------------------------------------------------------------------------

def analyze_collections(
    chroma_client: chromadb.HttpClient,
) -> Dict[str, Any]:
    """
    Analyze ChromaDB collection health and size distribution.

    Returns per-collection chunk counts, empty collections,
    and recommendations.
    """
    collections = chroma_client.list_collections()
    analysis = {}
    empty = []
    total = 0

    for col in collections:
        count = col.count()
        analysis[col.name] = {"chunks": count}
        total += count
        if count == 0:
            empty.append(col.name)

    # Check for expected domain collections
    expected = {f"domain_{d}" for d in config.DOMAINS}
    existing = {col.name for col in collections}
    missing = expected - existing
    extra = existing - expected

    recommendations = []
    if empty:
        recommendations.append(f"{len(empty)} empty collection(s): {', '.join(empty)}")
    if missing:
        recommendations.append(f"Missing domain collections: {', '.join(missing)}")
    if extra:
        recommendations.append(f"Unexpected collections: {', '.join(extra)}")

    return {
        "collections": analysis,
        "total_chunks": total,
        "empty_collections": empty,
        "missing_collections": list(missing),
        "extra_collections": list(extra),
        "recommendations": recommendations,
    }


# ---------------------------------------------------------------------------
# Main maintenance function
# ---------------------------------------------------------------------------

async def maintain(
    neo4j_driver,
    chroma_client: chromadb.HttpClient,
    redis_client,
    actions: Optional[List[str]] = None,
    stale_days: int = 90,
    auto_purge: bool = False,
) -> Dict[str, Any]:
    """
    Run maintenance routines on the knowledge base.

    Args:
        neo4j_driver: Neo4j driver instance
        chroma_client: ChromaDB client instance
        redis_client: Redis client instance
        actions: List of actions to run. Default: all.
            Options: "health", "stale", "collections", "orphans"
        stale_days: Days threshold for stale detection
        auto_purge: If True, automatically purge stale artifacts

    Returns:
        Maintenance report
    """
    all_actions = {"health", "stale", "collections", "orphans"}
    if actions is None:
        actions = list(all_actions)

    report = {
        "timestamp": datetime.utcnow().isoformat(),
        "actions_run": actions,
        "auto_purge": auto_purge,
    }

    # System health
    if "health" in actions:
        health = check_system_health(neo4j_driver, chroma_client, redis_client)
        # Add async Bifrost check
        bifrost_status = await check_bifrost_health()
        health["services"]["bifrost"] = bifrost_status
        health["overall"] = "healthy" if all(
            v == "connected" for v in health["services"].values()
        ) else "degraded"
        report["health"] = health

    # Stale artifacts
    if "stale" in actions:
        stale = find_stale_artifacts(neo4j_driver, days_threshold=stale_days)
        report["stale"] = {
            "count": len(stale),
            "threshold_days": stale_days,
            "artifacts": [
                {
                    "id": a["id"],
                    "filename": a["filename"],
                    "domain": a["domain"],
                    "ingested_at": a["ingested_at"],
                }
                for a in stale
            ],
        }

        if auto_purge and stale:
            purge_ids = [a["id"] for a in stale]
            purge_result = purge_artifacts(
                neo4j_driver, chroma_client, purge_ids, redis_client
            )
            report["purge_result"] = purge_result

    # Collection analysis
    if "collections" in actions:
        report["collections"] = analyze_collections(chroma_client)

    # Orphan check (delegates to rectify)
    if "orphans" in actions:
        from agents.rectify import cleanup_orphaned_chunks, find_orphaned_chunks
        orphans = find_orphaned_chunks(neo4j_driver, chroma_client)
        total_orphans = sum(len(v) for v in orphans.values())
        report["orphans"] = {
            "count": total_orphans,
            "by_domain": {d: len(chunks) for d, chunks in orphans.items()},
        }
        if auto_purge and total_orphans > 0:
            cleaned = cleanup_orphaned_chunks(chroma_client, orphans)
            report["orphan_cleanup"] = cleaned

    # Audit log
    try:
        log_event(
            redis_client,
            event_type="maintenance",
            artifact_id="",
            domain="all",
            filename="",
            extra={
                "actions": actions,
                "auto_purge": auto_purge,
            },
        )
    except Exception:
        pass

    return report
