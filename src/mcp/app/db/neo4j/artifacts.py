# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j artifact CRUD operations."""

from __future__ import annotations

import json
import logging
from typing import Any

import config
from utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.graph")


def create_artifact(
    driver,
    artifact_id: str,
    filename: str,
    domain: str,
    keywords_json: str,
    summary: str,
    chunk_count: int,
    chunk_ids_json: str,
    content_hash: str = "",
    sub_category: str = "",
    tags_json: str = "[]",
    quality_score: float = 0.5,
    client_source: str = "",
) -> str:
    """Create an Artifact node and link it to its Domain (and optionally SubCategory/Tags)."""
    sub_cat = sub_category or config.DEFAULT_SUB_CATEGORY
    now = utcnow_iso()

    with driver.session() as session:
        result = session.run(
            """
            MERGE (d:Domain {name: $domain})
            CREATE (a:Artifact {
                id: $artifact_id,
                filename: $filename,
                domain: $domain,
                sub_category: $sub_category,
                tags: $tags_json,
                keywords: $keywords_json,
                summary: $summary,
                chunk_count: $chunk_count,
                chunk_ids: $chunk_ids_json,
                content_hash: $content_hash,
                quality_score: $quality_score,
                client_source: $client_source,
                ingested_at: $ingested_at,
                updated_at: $ingested_at
            })
            CREATE (a)-[:BELONGS_TO]->(d)
            RETURN a.id AS id
            """,
            artifact_id=artifact_id,
            filename=filename,
            domain=domain,
            sub_category=sub_cat,
            tags_json=tags_json,
            keywords_json=keywords_json,
            summary=summary,
            chunk_count=chunk_count,
            chunk_ids_json=chunk_ids_json,
            content_hash=content_hash,
            quality_score=quality_score,
            client_source=client_source,
            ingested_at=now,
        )
        record = result.single()
        aid = record["id"] if record else artifact_id

        # Link to SubCategory node
        sc_name = f"{domain}/{sub_cat}"
        session.run(
            "MATCH (a:Artifact {id: $aid}), (sc:SubCategory {name: $sc_name}) "
            "MERGE (a)-[:CATEGORIZED_AS]->(sc)",
            aid=aid,
            sc_name=sc_name,
        )

        # Link to Tag nodes (batched — single query for all tags)
        try:
            tag_list = json.loads(tags_json) if tags_json else []
        except (json.JSONDecodeError, TypeError):
            tag_list = []
        clean_tags = [t.strip().lower() for t in tag_list if t.strip()]
        if clean_tags:
            session.run(
                "UNWIND $tags AS tag_name "
                "MERGE (t:Tag {name: tag_name}) "
                "ON CREATE SET t.created_at = $now, t.usage_count = 1 "
                "ON MATCH SET t.usage_count = t.usage_count + 1 "
                "WITH t "
                "MATCH (a:Artifact {id: $aid}) "
                "MERGE (a)-[:TAGGED_WITH]->(t)",
                tags=clean_tags,
                aid=aid,
                now=now,
            )

        return aid


def find_artifact_by_filename(
    driver,
    filename: str,
    domain: str,
) -> dict[str, Any] | None:
    """Find an existing artifact by filename and domain."""
    with driver.session() as session:
        result = session.run(
            "MATCH (a:Artifact {filename: $filename, domain: $domain}) "
            "RETURN a.id AS id, a.content_hash AS content_hash, "
            "a.chunk_ids AS chunk_ids",
            filename=filename,
            domain=domain,
        )
        record = result.single()
        if not record:
            return None
        return {
            "id": record["id"],
            "content_hash": record["content_hash"],
            "chunk_ids": record["chunk_ids"],
        }


def update_artifact(
    driver,
    artifact_id: str,
    keywords_json: str,
    summary: str,
    chunk_count: int,
    chunk_ids_json: str,
    content_hash: str,
    quality_score: float | None = None,
) -> None:
    """Update an existing artifact's content fields on re-ingestion."""
    with driver.session() as session:
        query = """
            MATCH (a:Artifact {id: $artifact_id})
            SET a.keywords = $keywords_json,
                a.summary = $summary,
                a.chunk_count = $chunk_count,
                a.chunk_ids = $chunk_ids_json,
                a.content_hash = $content_hash,
                a.modified_at = $modified_at,
                a.updated_at = $modified_at
            """
        params: dict[str, Any] = {
            "artifact_id": artifact_id,
            "keywords_json": keywords_json,
            "summary": summary,
            "chunk_count": chunk_count,
            "chunk_ids_json": chunk_ids_json,
            "content_hash": content_hash,
            "modified_at": utcnow_iso(),
        }
        if quality_score is not None:
            query += ", a.quality_score = $quality_score"
            params["quality_score"] = quality_score
        session.run(query, **params)
    logger.info(f"Updated artifact {artifact_id[:8]} (re-ingestion)")


def get_artifact(driver, artifact_id: str) -> dict[str, Any] | None:
    """Fetch a single artifact by ID."""
    with driver.session() as session:
        result = session.run(
            "MATCH (a:Artifact {id: $artifact_id})-[:BELONGS_TO]->(d:Domain) "
            "RETURN a.id AS id, a.filename AS filename, a.domain AS domain, "
            "a.sub_category AS sub_category, a.tags AS tags, "
            "a.keywords AS keywords, a.summary AS summary, "
            "a.chunk_count AS chunk_count, a.chunk_ids AS chunk_ids, "
            "a.ingested_at AS ingested_at, a.recategorized_at AS recategorized_at, "
            "d.name AS domain_name",
            artifact_id=artifact_id,
        )
        record = result.single()
        if not record:
            return None
        return {
            "id": record["id"],
            "filename": record["filename"],
            "domain": record["domain_name"],
            "sub_category": record["sub_category"] or config.DEFAULT_SUB_CATEGORY,
            "tags": record["tags"] or "[]",
            "keywords": record["keywords"],
            "summary": record["summary"],
            "chunk_count": record["chunk_count"],
            "chunk_ids": record["chunk_ids"],
            "ingested_at": record["ingested_at"],
            "recategorized_at": record["recategorized_at"],
        }


def list_artifacts(
    driver,
    domain: str | None = None,
    sub_category: str | None = None,
    tag: str | None = None,
    client_source: str | None = None,
    since: str | None = None,
    min_quality: float | None = None,
    offset: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List artifacts, optionally filtered by domain, sub_category, tag, client_source, date, and quality."""
    base_query = "MATCH (a:Artifact)-[:BELONGS_TO]->(d:Domain) "
    conditions = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if domain:
        conditions.append("d.name = $domain")
        params["domain"] = domain
    if sub_category:
        conditions.append("a.sub_category = $sub_category")
        params["sub_category"] = sub_category
    if tag:
        # Join against Tag node
        base_query = (
            "MATCH (a:Artifact)-[:BELONGS_TO]->(d:Domain), "
            "(a)-[:TAGGED_WITH]->(t:Tag {name: $tag}) "
        )
        params["tag"] = tag.strip().lower()
    if client_source:
        conditions.append("a.client_source = $client_source")
        params["client_source"] = client_source
    if since:
        conditions.append("a.ingested_at >= $since")
        params["since"] = since
    if min_quality is not None:
        conditions.append("a.quality_score >= $min_quality")
        params["min_quality"] = min_quality

    if conditions:
        base_query += "WHERE " + " AND ".join(conditions) + " "

    base_query += (
        "RETURN a.id AS id, a.filename AS filename, a.domain AS domain, "
        "a.sub_category AS sub_category, a.tags AS tags, "
        "a.keywords AS keywords, a.summary AS summary, "
        "a.chunk_count AS chunk_count, a.chunk_ids AS chunk_ids, "
        "a.ingested_at AS ingested_at, a.recategorized_at AS recategorized_at, "
        "a.quality_score AS quality_score, a.client_source AS client_source, "
        "d.name AS domain_name "
        "ORDER BY a.ingested_at DESC SKIP $offset LIMIT $limit"
    )

    with driver.session() as session:
        result = session.run(base_query, **params)
        return [
            {
                "id": record["id"],
                "filename": record["filename"],
                "domain": record["domain_name"],
                "sub_category": record["sub_category"] or config.DEFAULT_SUB_CATEGORY,
                "tags": record["tags"] or "[]",
                "keywords": record["keywords"],
                "summary": record["summary"],
                "chunk_count": record["chunk_count"],
                "chunk_ids": record["chunk_ids"],
                "ingested_at": record["ingested_at"],
                "recategorized_at": record["recategorized_at"],
                "quality_score": record["quality_score"],
                "client_source": record["client_source"] or "",
            }
            for record in result
        ]


def get_quality_scores(
    driver,
    artifact_ids: list[str],
) -> dict[str, float]:
    """Batch-fetch quality_score for artifact IDs. Returns {id: score}.

    Unscored artifacts default to 0.5 (neutral).
    """
    if not artifact_ids:
        return {}
    with driver.session() as session:
        result = session.run(
            "UNWIND $ids AS aid "
            "MATCH (a:Artifact {id: aid}) "
            "RETURN a.id AS id, a.quality_score AS score",
            ids=artifact_ids,
        )
        return {
            record["id"]: record["score"] if record["score"] is not None else 0.5
            for record in result
        }


def get_artifact_summaries(
    driver,
    artifact_ids: list[str],
) -> dict[str, str]:
    """Batch-fetch summary for artifact IDs. Returns {id: summary}."""
    if not artifact_ids:
        return {}
    with driver.session() as session:
        result = session.run(
            "UNWIND $ids AS aid "
            "MATCH (a:Artifact {id: aid}) "
            "RETURN a.id AS id, a.summary AS summary",
            ids=artifact_ids,
        )
        return {
            record["id"]: record["summary"]
            for record in result
            if record["summary"]
        }


def get_quality_and_summaries(
    driver,
    artifact_ids: list[str],
) -> tuple[dict[str, float], dict[str, str]]:
    """Batch-fetch quality_score AND summary in a single Cypher round-trip.

    Returns ``(scores_dict, summaries_dict)`` with the same semantics as
    :func:`get_quality_scores` and :func:`get_artifact_summaries`.
    """
    if not artifact_ids:
        return {}, {}
    with driver.session() as session:
        result = session.run(
            "UNWIND $ids AS aid "
            "MATCH (a:Artifact {id: aid}) "
            "RETURN a.id AS id, a.quality_score AS score, a.summary AS summary",
            ids=artifact_ids,
        )
        scores: dict[str, float] = {}
        summaries: dict[str, str] = {}
        for record in result:
            scores[record["id"]] = record["score"] if record["score"] is not None else 0.5
            if record["summary"]:
                summaries[record["id"]] = record["summary"]
        return scores, summaries


def update_artifact_summary(
    driver,
    artifact_id: str,
    summary: str,
) -> bool:
    """Update an artifact's summary text. Returns True if the artifact was found."""
    now = utcnow_iso()
    with driver.session() as session:
        result = session.run(
            "MATCH (a:Artifact {id: $aid}) "
            "SET a.summary = $summary, a.modified_at = $now, a.updated_at = $now "
            "RETURN a.id AS id",
            aid=artifact_id,
            summary=summary,
            now=now,
        )
        return result.single() is not None


def delete_artifact(
    driver,
    artifact_id: str,
) -> dict[str, Any]:
    """Delete an artifact and all its relationships. Returns deletion details."""
    with driver.session() as session:
        # Fetch chunk_ids before deletion (needed for tombstone + ChromaDB cleanup)
        result = session.run(
            "MATCH (a:Artifact {id: $id}) "
            "RETURN a.chunk_ids AS chunk_ids, a.domain AS domain, a.filename AS filename",
            id=artifact_id,
        )
        record = result.single()
        if not record:
            return {"deleted": False, "reason": "not_found"}

        chunk_ids_raw = record["chunk_ids"] or "[]"
        try:
            chunk_ids = json.loads(chunk_ids_raw) if isinstance(chunk_ids_raw, str) else chunk_ids_raw
        except (json.JSONDecodeError, TypeError):
            chunk_ids = []

        domain = record["domain"] or ""
        filename = record["filename"] or ""

        # Delete the artifact and all relationships
        session.run(
            "MATCH (a:Artifact {id: $id}) DETACH DELETE a",
            id=artifact_id,
        )

    logger.info("Deleted artifact %s (%s)", artifact_id[:8], filename)
    return {
        "deleted": True,
        "artifact_id": artifact_id,
        "domain": domain,
        "filename": filename,
        "chunk_ids": chunk_ids,
    }


def get_active_memories(
    driver,
    domain: str = "conversations",
    memory_type: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fetch memories that have NOT been superseded.

    Filters on ``superseded_by IS NULL`` to exclude stale/replaced entries.
    Optionally filter by memory_type metadata.
    """
    conditions = [
        "d.name = $domain",
        "a.superseded_by IS NULL",
    ]
    params: dict[str, Any] = {"domain": domain, "limit": limit}

    if memory_type:
        conditions.append("a.memory_type = $memory_type")
        params["memory_type"] = memory_type

    where_clause = " AND ".join(conditions)

    query = (
        f"MATCH (a:Artifact)-[:BELONGS_TO]->(d:Domain) "
        f"WHERE {where_clause} "
        "RETURN a.id AS id, a.filename AS filename, a.summary AS summary, "
        "a.memory_type AS memory_type, a.valid_from AS valid_from, "
        "a.ingested_at AS ingested_at "
        "ORDER BY a.ingested_at DESC LIMIT $limit"
    )

    with driver.session() as session:
        result = session.run(query, **params)
        return [
            {
                "id": record["id"],
                "filename": record["filename"],
                "summary": record["summary"],
                "memory_type": record["memory_type"],
                "valid_from": record["valid_from"],
                "ingested_at": record["ingested_at"],
            }
            for record in result
        ]


def recategorize_artifact(
    driver,
    artifact_id: str,
    new_domain: str,
) -> dict[str, str]:
    """Move an artifact's BELONGS_TO relationship to a new Domain."""
    with driver.session() as session:
        result = session.run(
            """
            MATCH (a:Artifact {id: $artifact_id})-[r:BELONGS_TO]->(old:Domain)
            DELETE r
            MERGE (new:Domain {name: $new_domain})
            CREATE (a)-[:BELONGS_TO]->(new)
            SET a.domain = $new_domain,
                a.recategorized_at = $now,
                a.updated_at = $now
            RETURN old.name AS old_domain, new.name AS new_domain
            """,
            artifact_id=artifact_id,
            new_domain=new_domain,
            now=utcnow_iso(),
        )
        record = result.single()
        if not record:
            raise ValueError(f"Artifact not found: {artifact_id}")
        return {
            "old_domain": record["old_domain"],
            "new_domain": record["new_domain"],
        }


# ---------------------------------------------------------------------------
# Verification report persistence
# ---------------------------------------------------------------------------

def save_verification_report(
    driver,
    conversation_id: str,
    claims: list[dict],
    overall_score: float,
    verified: int = 0,
    unverified: int = 0,
    uncertain: int = 0,
    total: int = 0,
) -> str:
    """Persist a verification report as a Neo4j node.

    Creates a ``VerificationReport`` node and ``VERIFIED`` relationships
    to any ``Artifact`` nodes referenced in the claims' sources.
    """
    import uuid

    report_id = str(uuid.uuid4())
    claims_json = json.dumps(claims)

    with driver.session() as session:
        # Upsert: one report per conversation (replace if re-verified)
        session.run(
            "MERGE (r:VerificationReport {conversation_id: $cid}) "
            "SET r.id = $rid, "
            "    r.claims = $claims, "
            "    r.overall_score = $score, "
            "    r.verified = $verified, "
            "    r.unverified = $unverified, "
            "    r.uncertain = $uncertain, "
            "    r.total = $total, "
            "    r.created_at = $now ",
            rid=report_id,
            cid=conversation_id,
            claims=claims_json,
            score=overall_score,
            verified=verified,
            unverified=unverified,
            uncertain=uncertain,
            total=total,
            now=utcnow_iso(),
        )

        # Create VERIFIED relationships to referenced artifacts
        artifact_ids = set()
        for claim in claims:
            for source in claim.get("sources", []):
                aid = source.get("artifact_id")
                if aid:
                    artifact_ids.add(aid)

        for aid in artifact_ids:
            try:
                session.run(
                    "MATCH (r:VerificationReport {conversation_id: $cid}) "
                    "MATCH (a:Artifact {id: $aid}) "
                    "MERGE (r)-[:VERIFIED]->(a)",
                    cid=conversation_id,
                    aid=aid,
                )
            except Exception as e:
                logger.debug("Failed to create VERIFIED relationship: %s", e)

    logger.info("Saved verification report %s for conversation %s", report_id[:8], conversation_id[:8])
    return report_id


def get_verification_report(driver, conversation_id: str) -> dict | None:
    """Retrieve a saved verification report by conversation ID."""
    with driver.session() as session:
        result = session.run(
            "MATCH (r:VerificationReport {conversation_id: $cid}) "
            "RETURN r.id AS id, r.conversation_id AS conversation_id, "
            "       r.claims AS claims, r.overall_score AS overall_score, "
            "       r.verified AS verified, r.unverified AS unverified, "
            "       r.uncertain AS uncertain, r.total AS total, "
            "       r.created_at AS created_at",
            cid=conversation_id,
        )
        record = result.single()
        if not record:
            return None

        claims = []
        try:
            claims = json.loads(record["claims"])
        except (json.JSONDecodeError, TypeError):
            pass

        return {
            "report_id": record["id"],
            "conversation_id": record["conversation_id"],
            "claims": claims,
            "overall_score": record["overall_score"],
            "verified": record["verified"],
            "unverified": record["unverified"],
            "uncertain": record["uncertain"],
            "total": record["total"],
            "created_at": record["created_at"],
        }
