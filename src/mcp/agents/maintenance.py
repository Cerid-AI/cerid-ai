# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Maintenance Agent — scheduled health checks and automated cleanup."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

import config
from agents.rectify import find_stale_artifacts
from errors import CeridError
from utils.cache import log_event
from utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.maintenance")


# ---------------------------------------------------------------------------
# System health
# ---------------------------------------------------------------------------

def check_system_health(
    neo4j_driver,
    chroma_client: Any,
    redis_client,
) -> dict[str, Any]:
    """Comprehensive health check — data integrity, collection counts, Bifrost reachability."""
    health: dict[str, Any] = {
        "timestamp": utcnow_iso(),
        "services": {},
        "data": {},
    }

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
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        health["services"]["chromadb"] = f"error: {e}"

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
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        health["services"]["neo4j"] = f"error: {e}"

    try:
        redis_client.ping()
        log_size = redis_client.llen(config.REDIS_INGEST_LOG)
        health["services"]["redis"] = "connected"
        health["data"]["audit_log_entries"] = log_size
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        health["services"]["redis"] = f"error: {e}"

    # Bifrost check is async-only; callers in async context (maintain())
    # overwrite this with the real result after awaiting _check_bifrost_async().
    health["services"]["bifrost"] = "skipped (sync context)"

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
        with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310 — URL from config, not user input
            if resp.status == 200:
                return "connected"
            return f"status {resp.status}"
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
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
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        return f"unreachable: {e}"


# ---------------------------------------------------------------------------
# Stale artifact management
# ---------------------------------------------------------------------------

def purge_artifacts(
    neo4j_driver,
    chroma_client: Any,
    artifact_ids: list[str],
    redis_client=None,
) -> dict[str, Any]:
    """Remove specified artifacts from Neo4j and ChromaDB."""
    purged = []
    errors = []

    for artifact_id in artifact_ids:
        try:
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

            if chunk_ids:
                try:
                    collection = chroma_client.get_collection(name=config.collection_name(domain))
                    collection.delete(ids=chunk_ids)
                except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
                    logger.warning(f"Failed to delete chunks for {artifact_id}: {e}")

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

            if redis_client:
                try:
                    log_event(
                        redis_client,
                        event_type="maintenance_purge",
                        artifact_id=artifact_id,
                        domain=domain,
                        filename=filename,
                    )
                except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
                    logger.debug(f"Failed to log maintenance purge event: {e}")

        except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
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
    chroma_client: Any,
) -> dict[str, Any]:
    """Analyze ChromaDB collection health and size distribution."""
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

    expected = {config.collection_name(d) for d in config.DOMAINS}
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
    chroma_client: Any,
    redis_client,
    actions: list[str] | None = None,
    stale_days: int = 90,
    auto_purge: bool = False,
) -> dict[str, Any]:
    """Run maintenance routines on the knowledge base."""
    all_actions = {"health", "stale", "collections", "orphans"}
    if actions is None:
        actions = list(all_actions)

    report: dict[str, Any] = {
        "timestamp": utcnow_iso(),
        "actions_run": actions,
        "auto_purge": auto_purge,
    }

    if "health" in actions:
        health = check_system_health(neo4j_driver, chroma_client, redis_client)
        bifrost_status = await check_bifrost_health()
        health["services"]["bifrost"] = bifrost_status
        health["overall"] = "healthy" if all(
            v == "connected" for v in health["services"].values()
        ) else "degraded"
        report["health"] = health

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

    if "collections" in actions:
        report["collections"] = analyze_collections(chroma_client)

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
    except (CeridError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        logger.debug(f"Failed to log maintenance event: {e}")

    return report
