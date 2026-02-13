"""
Neo4j operations for artifact management.

All Cypher queries are isolated here. main.py calls these functions
and never runs Cypher directly.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger("ai-companion.graph")


def init_schema(driver) -> None:
    """
    Create constraints and seed Domain nodes. Idempotent — safe to call on every startup.
    """
    with driver.session() as session:
        session.run(
            "CREATE CONSTRAINT artifact_id IF NOT EXISTS "
            "FOR (a:Artifact) REQUIRE a.id IS UNIQUE"
        )
        session.run(
            "CREATE CONSTRAINT domain_name IF NOT EXISTS "
            "FOR (d:Domain) REQUIRE d.name IS UNIQUE"
        )
        # Drop the old index if it exists (being replaced by unique constraint)
        try:
            session.run("DROP INDEX artifact_content_hash IF EXISTS")
        except Exception:
            pass  # index may not exist
        session.run(
            "CREATE CONSTRAINT artifact_content_hash_unique IF NOT EXISTS "
            "FOR (a:Artifact) REQUIRE a.content_hash IS UNIQUE"
        )
        for domain in config.DOMAINS:
            session.run("MERGE (:Domain {name: $name})", name=domain)
    logger.info(f"Neo4j schema initialized with {len(config.DOMAINS)} domains")


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
) -> str:
    """Create an Artifact node and link it to its Domain."""
    with driver.session() as session:
        result = session.run(
            """
            MERGE (d:Domain {name: $domain})
            CREATE (a:Artifact {
                id: $artifact_id,
                filename: $filename,
                domain: $domain,
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
            keywords_json=keywords_json,
            summary=summary,
            chunk_count=chunk_count,
            chunk_ids_json=chunk_ids_json,
            content_hash=content_hash,
            ingested_at=datetime.utcnow().isoformat(),
        )
        record = result.single()
        return record["id"] if record else artifact_id


def get_artifact(driver, artifact_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single artifact by ID."""
    with driver.session() as session:
        result = session.run(
            "MATCH (a:Artifact {id: $artifact_id})-[:BELONGS_TO]->(d:Domain) "
            "RETURN a.id AS id, a.filename AS filename, a.domain AS domain, "
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
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """List artifacts, optionally filtered by domain."""
    base_query = (
        "MATCH (a:Artifact)-[:BELONGS_TO]->(d:Domain) "
    )
    if domain:
        base_query += "WHERE d.name = $domain "
    base_query += (
        "RETURN a.id AS id, a.filename AS filename, a.domain AS domain, "
        "a.keywords AS keywords, a.summary AS summary, "
        "a.chunk_count AS chunk_count, a.chunk_ids AS chunk_ids, "
        "a.ingested_at AS ingested_at, a.recategorized_at AS recategorized_at, "
        "d.name AS domain_name "
        "ORDER BY a.ingested_at DESC LIMIT $limit"
    )
    params: Dict[str, Any] = {"limit": limit}
    if domain:
        params["domain"] = domain

    with driver.session() as session:
        result = session.run(base_query, **params)
        return [
            {
                "id": record["id"],
                "filename": record["filename"],
                "domain": record["domain_name"],
                "keywords": record["keywords"],
                "summary": record["summary"],
                "chunk_count": record["chunk_count"],
                "chunk_ids": record["chunk_ids"],
                "ingested_at": record["ingested_at"],
                "recategorized_at": record["recategorized_at"],
            }
            for record in result
        ]


def recategorize_artifact(
    driver,
    artifact_id: str,
    new_domain: str,
) -> Dict[str, str]:
    """
    Move an artifact's BELONGS_TO relationship to a new Domain.

    Returns {"old_domain": ..., "new_domain": ...}
    """
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
            now=datetime.utcnow().isoformat(),
        )
        record = result.single()
        if not record:
            raise ValueError(f"Artifact not found: {artifact_id}")
        return {
            "old_domain": record["old_domain"],
            "new_domain": record["new_domain"],
        }
