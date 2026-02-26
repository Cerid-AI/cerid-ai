"""
Rectification Agent - Detect and resolve conflicts in the knowledge graph.

Provides:
- Duplicate detection across domains (same content in multiple collections)
- Stale artifact detection (old content that may need refresh)
- Orphaned chunk cleanup (ChromaDB chunks without Neo4j artifact records)
- Relationship consistency checks between Neo4j and ChromaDB
- AI-assisted conflict resolution via Bifrost
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

import chromadb

from utils.time import utcnow, utcnow_iso

import config
from utils.cache import log_event

logger = logging.getLogger("ai-companion.rectify")


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

def find_duplicate_artifacts(
    neo4j_driver,
    chroma_client: Optional[chromadb.HttpClient] = None,
) -> List[Dict[str, Any]]:
    """
    Find artifacts that share the same content_hash but exist in different domains.

    These are true duplicates that were ingested before cross-domain dedup was in place,
    or were re-ingested after a recategorization.
    """
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
) -> List[Dict[str, Any]]:
    """
    Find artifacts in a domain that are semantically similar to the given text.

    Uses ChromaDB cosine distance. Results with distance < threshold are
    considered potential conflicts (near-duplicates or outdated versions).
    """
    collection_name = f"domain_{domain}"
    try:
        collection = chroma_client.get_collection(name=collection_name)
    except Exception:
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
) -> List[Dict[str, Any]]:
    """
    Find artifacts that haven't been updated in a long time.

    These may contain outdated information and should be flagged for review.
    """
    cutoff = (utcnow().replace(tzinfo=None) - timedelta(days=days_threshold)).isoformat()

    with neo4j_driver.session() as session:
        result = session.run(
            """
            MATCH (a:Artifact)-[:BELONGS_TO]->(d:Domain)
            WHERE a.ingested_at < $cutoff
            AND (a.recategorized_at IS NULL OR a.recategorized_at < $cutoff)
            RETURN a.id AS id, a.filename AS filename, d.name AS domain,
                   a.ingested_at AS ingested_at, a.chunk_count AS chunk_count
            ORDER BY a.ingested_at ASC
            LIMIT 100
            """,
            cutoff=cutoff,
        )
        return [
            {
                "id": record["id"],
                "filename": record["filename"],
                "domain": record["domain"],
                "ingested_at": record["ingested_at"],
                "chunk_count": record["chunk_count"],
                "age_indicator": "stale",
            }
            for record in result
        ]


def find_orphaned_chunks(
    neo4j_driver,
    chroma_client: chromadb.HttpClient,
) -> Dict[str, Any]:
    """
    Find ChromaDB chunks that don't have corresponding Neo4j artifact records.

    This can happen after failed ingestions or interrupted recategorizations.
    """
    # Get all artifact IDs from Neo4j
    with neo4j_driver.session() as session:
        result = session.run("MATCH (a:Artifact) RETURN a.id AS id")
        neo4j_ids = {record["id"] for record in result}

    # Check each domain collection for orphaned chunks
    orphaned = {}
    for domain in config.DOMAINS:
        collection_name = f"domain_{domain}"
        try:
            collection = chroma_client.get_collection(name=collection_name)
        except Exception:
            continue

        # Get all chunks in this collection
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
) -> Dict[str, Any]:
    """
    Resolve a duplicate set by keeping one artifact and removing the rest.

    Args:
        content_hash: The shared content hash
        keep_artifact_id: The artifact ID to keep
        redis_client: Optional Redis client for audit logging

    Returns:
        Summary of removed artifacts
    """
    # Find all artifacts with this hash
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

        # Remove chunks from ChromaDB
        chunk_ids = json.loads(artifact.get("chunk_ids", "[]"))
        if chunk_ids:
            collection_name = f"domain_{artifact['domain']}"
            try:
                collection = chroma_client.get_collection(name=collection_name)
                collection.delete(ids=chunk_ids)
            except Exception as e:
                logger.warning(f"Failed to delete chunks for {artifact['id']}: {e}")

        # Remove from Neo4j
        with neo4j_driver.session() as session:
            session.run(
                """
                MATCH (a:Artifact {id: $id})
                DETACH DELETE a
                """,
                id=artifact["id"],
            )

        removed.append({
            "id": artifact["id"],
            "filename": artifact["filename"],
            "domain": artifact["domain"],
        })

        # Audit log
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
            except Exception:
                pass

    return {
        "kept": keep_artifact_id,
        "removed": removed,
        "removed_count": len(removed),
    }


def cleanup_orphaned_chunks(
    chroma_client: chromadb.HttpClient,
    orphaned: Dict[str, List[Dict[str, str]]],
) -> Dict[str, int]:
    """
    Remove orphaned chunks from ChromaDB.

    Args:
        orphaned: Output from find_orphaned_chunks()

    Returns:
        Count of removed chunks per domain
    """
    cleaned = {}
    for domain, chunks in orphaned.items():
        chunk_ids = [c["chunk_id"] for c in chunks]
        if not chunk_ids:
            continue

        collection_name = f"domain_{domain}"
        try:
            collection = chroma_client.get_collection(name=collection_name)
            collection.delete(ids=chunk_ids)
            cleaned[domain] = len(chunk_ids)
        except Exception as e:
            logger.error(f"Failed to clean orphans in {domain}: {e}")
            cleaned[domain] = 0

    return cleaned


# ---------------------------------------------------------------------------
# Graph relationship analysis
# ---------------------------------------------------------------------------

def analyze_domain_distribution(neo4j_driver) -> Dict[str, Any]:
    """
    Analyze the distribution of artifacts across domains.

    Returns per-domain counts, total artifacts, and any imbalances.
    """
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

    # Add empty domains
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
    checks: Optional[List[str]] = None,
    auto_fix: bool = False,
    stale_days: int = 90,
) -> Dict[str, Any]:
    """
    Run rectification checks on the knowledge base.

    Args:
        neo4j_driver: Neo4j driver instance
        chroma_client: ChromaDB client instance
        redis_client: Optional Redis client for audit logging
        checks: List of checks to run. Default: all checks.
            Options: "duplicates", "stale", "orphans", "distribution"
        auto_fix: If True, automatically resolve duplicates and clean orphans
        stale_days: Days threshold for stale artifact detection

    Returns:
        Rectification report with findings and actions taken
    """
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

    # Duplicates
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

    # Stale artifacts
    if "stale" in checks:
        stale = find_stale_artifacts(neo4j_driver, days_threshold=stale_days)
        report["findings"]["stale"] = {
            "count": len(stale),
            "threshold_days": stale_days,
            "artifacts": stale,
        }

    # Orphaned chunks
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

    # Distribution analysis
    if "distribution" in checks:
        dist = analyze_domain_distribution(neo4j_driver)
        report["findings"]["distribution"] = dist

    # Audit log
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
        except Exception:
            pass

    return report
