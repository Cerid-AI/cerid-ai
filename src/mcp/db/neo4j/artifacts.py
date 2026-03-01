# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j artifact CRUD operations."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

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
                ingested_at: $ingested_at
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
) -> Optional[Dict[str, Any]]:
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
) -> None:
    """Update an existing artifact's content fields on re-ingestion."""
    with driver.session() as session:
        session.run(
            """
            MATCH (a:Artifact {id: $artifact_id})
            SET a.keywords = $keywords_json,
                a.summary = $summary,
                a.chunk_count = $chunk_count,
                a.chunk_ids = $chunk_ids_json,
                a.content_hash = $content_hash,
                a.modified_at = $modified_at
            """,
            artifact_id=artifact_id,
            keywords_json=keywords_json,
            summary=summary,
            chunk_count=chunk_count,
            chunk_ids_json=chunk_ids_json,
            content_hash=content_hash,
            modified_at=utcnow_iso(),
        )
    logger.info(f"Updated artifact {artifact_id[:8]} (re-ingestion)")


def get_artifact(driver, artifact_id: str) -> Optional[Dict[str, Any]]:
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
    domain: Optional[str] = None,
    sub_category: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """List artifacts, optionally filtered by domain, sub_category, and/or tag."""
    base_query = "MATCH (a:Artifact)-[:BELONGS_TO]->(d:Domain) "
    conditions = []
    params: Dict[str, Any] = {"limit": limit}

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

    if conditions:
        base_query += "WHERE " + " AND ".join(conditions) + " "

    base_query += (
        "RETURN a.id AS id, a.filename AS filename, a.domain AS domain, "
        "a.sub_category AS sub_category, a.tags AS tags, "
        "a.keywords AS keywords, a.summary AS summary, "
        "a.chunk_count AS chunk_count, a.chunk_ids AS chunk_ids, "
        "a.ingested_at AS ingested_at, a.recategorized_at AS recategorized_at, "
        "d.name AS domain_name "
        "ORDER BY a.ingested_at DESC LIMIT $limit"
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
            }
            for record in result
        ]


def get_quality_scores(
    driver,
    artifact_ids: List[str],
) -> Dict[str, float]:
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


def update_artifact_summary(
    driver,
    artifact_id: str,
    summary: str,
) -> bool:
    """Update an artifact's summary text. Returns True if the artifact was found."""
    with driver.session() as session:
        result = session.run(
            "MATCH (a:Artifact {id: $aid}) "
            "SET a.summary = $summary, a.modified_at = $now "
            "RETURN a.id AS id",
            aid=artifact_id,
            summary=summary,
            now=utcnow_iso(),
        )
        return result.single() is not None


def recategorize_artifact(
    driver,
    artifact_id: str,
    new_domain: str,
) -> Dict[str, str]:
    """Move an artifact's BELONGS_TO relationship to a new Domain."""
    with driver.session() as session:
        result = session.run(
            """
            MATCH (a:Artifact {id: $artifact_id})-[r:BELONGS_TO]->(old:Domain)
            DELETE r
            MERGE (new:Domain {name: $new_domain})
            CREATE (a)-[:BELONGS_TO]->(new)
            SET a.domain = $new_domain,
                a.recategorized_at = $now
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
