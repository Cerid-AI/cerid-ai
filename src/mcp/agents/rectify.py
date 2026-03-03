# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Rectification Agent — detect and resolve conflicts in the knowledge graph."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

import chromadb

import config
from utils.cache import log_event
from utils.time import utcnow, utcnow_iso

logger = logging.getLogger("ai-companion.rectify")


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

def find_duplicate_artifacts(
    neo4j_driver,
    chroma_client: chromadb.HttpClient | None = None,
) -> list[dict[str, Any]]:
    """Find artifacts sharing the same content_hash across different domains."""
    with neo4j_driver.session() as session:
        result = session.run(
            """
            MATCH (a:Artifact)-[:BELONGS_TO]->(d:Domain)
            WITH a.content_hash AS hash, collect({
                id: a.id, filename: a.filename, domain: d.name,
                ingested_at: a.ingested_at
            }) AS artifacts
            WHERE size(artifacts) > 1
            RETURN hash, artifacts
            ORDER BY size(artifacts) DESC
            """
        )
        duplicates = []
        for record in result:
            duplicates.append({
                "content_hash": record["hash"],
                "count": len(record["artifacts"]),
                "artifacts": record["artifacts"],
            })
    return duplicates


def find_similar_artifacts(
    query_text: str,
    domain: str,
    chroma_client: chromadb.HttpClient,
    threshold: float = 0.15,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Find semantically similar artifacts (cosine distance < threshold)."""
    try:
        collection = chroma_client.get_collection(name=config.collection_name(domain))
    except Exception as e:
        logger.debug(f"Collection not found for domain {domain}: {e}")
        return []

    results = collection.query(
        query_texts=[query_text[:2000]],
        n_results=top_k,
        include=["metadatas", "distances"],
    )

    similar = []
    if results["ids"] and results["ids"][0]:
        for i, chunk_id in enumerate(results["ids"][0]):
            distance = results["distances"][0][i]
            if distance < threshold:
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                similar.append({
                    "chunk_id": chunk_id,
                    "distance": round(distance, 4),
                    "artifact_id": meta.get("artifact_id", ""),
                    "filename": meta.get("filename", ""),
                    "domain": meta.get("domain", domain),
                })

    return similar


def find_stale_artifacts(
    neo4j_driver,
    days_threshold: int = 90,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Find artifacts not updated within the threshold period."""
    cutoff = (utcnow().replace(tzinfo=None) - timedelta(days=days_threshold)).isoformat()

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
        return [
            {
                "id": record["id"],
                "filename": record["filename"],
                "domain": record["domain"],
                "ingested_at": record["ingested_at"],
                "chunk_count": record["chunk_count"],
                "chunk_ids": record["chunk_ids"],
                "age_indicator": "stale",
            }
            for record in result
        ]


def find_orphaned_chunks(
    neo4j_driver,
    chroma_client: chromadb.HttpClient,
) -> dict[str, Any]:
    """Find ChromaDB chunks without corresponding Neo4j artifact records."""
    with neo4j_driver.session() as session:
        result = session.run("MATCH (a:Artifact) RETURN a.id AS id")
        neo4j_ids = {record["id"] for record in result}

    orphaned = {}
    for domain in config.DOMAINS:
        try:
            collection = chroma_client.get_collection(name=config.collection_name(domain))
        except Exception as e:
            logger.debug(f"Collection not found for domain {domain}: {e}")
            continue

        all_data = collection.get(include=["metadatas"])
        if not all_data["ids"]:
            continue

        domain_orphans = []
        for i, chunk_id in enumerate(all_data["ids"]):
            meta = all_data["metadatas"][i] if all_data["metadatas"] else {}
            artifact_id = meta.get("artifact_id", "")
            if artifact_id and artifact_id not in neo4j_ids:
                domain_orphans.append({
                    "chunk_id": chunk_id,
                    "artifact_id": artifact_id,
                    "filename": meta.get("filename", ""),
                })

        if domain_orphans:
            orphaned[domain] = domain_orphans

    return orphaned


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------

def resolve_duplicates(
    neo4j_driver,
    chroma_client: chromadb.HttpClient,
    content_hash: str,
    keep_artifact_id: str,
    redis_client=None,
) -> dict[str, Any]:
    """Resolve a duplicate set by keeping one artifact and removing the rest."""
    with neo4j_driver.session() as session:
        result = session.run(
            """
            MATCH (a:Artifact {content_hash: $hash})-[:BELONGS_TO]->(d:Domain)
            RETURN a.id AS id, a.filename AS filename, d.name AS domain,
                   a.chunk_ids AS chunk_ids
            """,
            hash=content_hash,
        )
        all_artifacts = [dict(record) for record in result]

    removed = []
    for artifact in all_artifacts:
        if artifact["id"] == keep_artifact_id:
            continue

        chunk_ids = json.loads(artifact.get("chunk_ids", "[]"))
        if chunk_ids:
            try:
                collection = chroma_client.get_collection(name=config.collection_name(artifact['domain']))
                collection.delete(ids=chunk_ids)
            except Exception as e:
                logger.warning(f"Failed to delete chunks for {artifact['id']}: {e}")

        try:
            with neo4j_driver.session() as session:
                session.run(
                    """
                    MATCH (a:Artifact {id: $id})
                    DETACH DELETE a
                    """,
                    id=artifact["id"],
                )
        except Exception as e:
            logger.error(f"Failed to delete artifact {artifact['id']} from Neo4j: {e}")
            continue

        removed.append({
            "id": artifact["id"],
            "filename": artifact["filename"],
            "domain": artifact["domain"],
        })

        if redis_client:
            try:
                log_event(
                    redis_client,
                    event_type="rectify_dedup",
                    artifact_id=artifact["id"],
                    domain=artifact["domain"],
                    filename=artifact["filename"],
                    extra={"kept": keep_artifact_id, "content_hash": content_hash},
                )
            except Exception as e:
                logger.debug(f"Failed to log rectify event: {e}")

    return {
        "kept": keep_artifact_id,
        "removed": removed,
        "removed_count": len(removed),
    }


def cleanup_orphaned_chunks(
    chroma_client: chromadb.HttpClient,
    orphaned: dict[str, list[dict[str, str]]],
) -> dict[str, int]:
    """Remove orphaned chunks from ChromaDB."""
    cleaned = {}
    for domain, chunks in orphaned.items():
        chunk_ids = [c["chunk_id"] for c in chunks]
        if not chunk_ids:
            continue

        try:
            collection = chroma_client.get_collection(name=config.collection_name(domain))
            collection.delete(ids=chunk_ids)
            cleaned[domain] = len(chunk_ids)
        except Exception as e:
            logger.error(f"Failed to clean orphans in {domain}: {e}")
            cleaned[domain] = 0

    return cleaned


# ---------------------------------------------------------------------------
# Graph relationship analysis
# ---------------------------------------------------------------------------

def analyze_domain_distribution(neo4j_driver) -> dict[str, Any]:
    """Analyze per-domain artifact distribution."""
    with neo4j_driver.session() as session:
        result = session.run(
            """
            MATCH (a:Artifact)-[:BELONGS_TO]->(d:Domain)
            RETURN d.name AS domain, count(a) AS count,
                   sum(a.chunk_count) AS total_chunks
            ORDER BY count DESC
            """
        )
        distribution = {}
        total_artifacts = 0
        total_chunks = 0
        for record in result:
            distribution[record["domain"]] = {
                "artifacts": record["count"],
                "chunks": record["total_chunks"] or 0,
            }
            total_artifacts += record["count"]
            total_chunks += (record["total_chunks"] or 0)

    for domain in config.DOMAINS:
        if domain not in distribution:
            distribution[domain] = {"artifacts": 0, "chunks": 0}

    return {
        "distribution": distribution,
        "total_artifacts": total_artifacts,
        "total_chunks": total_chunks,
        "domain_count": len(config.DOMAINS),
    }


# ---------------------------------------------------------------------------
# Main rectification function
# ---------------------------------------------------------------------------

async def rectify(
    neo4j_driver,
    chroma_client: chromadb.HttpClient,
    redis_client=None,
    checks: list[str] | None = None,
    auto_fix: bool = False,
    stale_days: int = 90,
) -> dict[str, Any]:
    """Run rectification checks on the knowledge base."""
    all_checks = {"duplicates", "stale", "orphans", "distribution"}
    if checks is None:
        checks = list(all_checks)

    report = {
        "timestamp": utcnow_iso(),
        "checks_run": checks,
        "auto_fix": auto_fix,
        "findings": {},
        "actions": [],
    }

    if "duplicates" in checks:
        dupes = find_duplicate_artifacts(neo4j_driver, chroma_client)
        report["findings"]["duplicates"] = {
            "count": len(dupes),
            "details": dupes,
        }

        if auto_fix and dupes:
            for dupe_set in dupes:
                # Keep the oldest artifact (first ingested)
                artifacts = sorted(
                    dupe_set["artifacts"],
                    key=lambda a: a.get("ingested_at", ""),
                )
                keep_id = artifacts[0]["id"]
                result = resolve_duplicates(
                    neo4j_driver, chroma_client,
                    dupe_set["content_hash"], keep_id, redis_client,
                )
                report["actions"].append({
                    "type": "dedup",
                    "content_hash": dupe_set["content_hash"],
                    **result,
                })

    if "stale" in checks:
        stale = find_stale_artifacts(neo4j_driver, days_threshold=stale_days)
        report["findings"]["stale"] = {
            "count": len(stale),
            "threshold_days": stale_days,
            "artifacts": stale,
        }

    if "orphans" in checks:
        orphans = find_orphaned_chunks(neo4j_driver, chroma_client)
        total_orphans = sum(len(v) for v in orphans.values())
        report["findings"]["orphans"] = {
            "count": total_orphans,
            "by_domain": {d: len(chunks) for d, chunks in orphans.items()},
        }

        if auto_fix and total_orphans > 0:
            cleaned = cleanup_orphaned_chunks(chroma_client, orphans)
            report["actions"].append({
                "type": "orphan_cleanup",
                "cleaned": cleaned,
            })

    if "distribution" in checks:
        dist = analyze_domain_distribution(neo4j_driver)
        report["findings"]["distribution"] = dist

    if redis_client:
        try:
            log_event(
                redis_client,
                event_type="rectify",
                artifact_id="",
                domain="all",
                filename="",
                extra={
                    "checks": checks,
                    "auto_fix": auto_fix,
                    "findings_summary": {
                        k: v.get("count", 0) for k, v in report["findings"].items()
                        if isinstance(v, dict) and "count" in v
                    },
                },
            )
        except Exception as e:
            logger.debug(f"Failed to log rectify event: {e}")

    return report
