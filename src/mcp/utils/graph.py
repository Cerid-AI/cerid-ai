"""
Neo4j operations for artifact management and knowledge graph traversal.

All Cypher queries are isolated here. Routers and agents call these
functions and never run Cypher directly.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

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
        # Index for relationship traversal performance
        session.run(
            "CREATE INDEX artifact_domain_idx IF NOT EXISTS "
            "FOR (a:Artifact) ON (a.domain)"
        )
        session.run(
            "CREATE INDEX artifact_filename_idx IF NOT EXISTS "
            "FOR (a:Artifact) ON (a.filename)"
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
    """Update an existing artifact's content fields (re-ingestion). Preserves relationships."""
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
            modified_at=datetime.utcnow().isoformat(),
        )
    logger.info(f"Updated artifact {artifact_id[:8]} (re-ingestion)")


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


# ---------------------------------------------------------------------------
# Relationship management (Phase 4B.2)
# ---------------------------------------------------------------------------

def create_relationship(
    driver,
    source_id: str,
    target_id: str,
    rel_type: str,
    properties: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Create a typed relationship between two artifacts. Idempotent — skips if
    the relationship already exists.

    Args:
        driver: Neo4j driver
        source_id: Source artifact ID
        target_id: Target artifact ID
        rel_type: One of config.GRAPH_RELATIONSHIP_TYPES
        properties: Optional relationship metadata (reason, score, etc.)

    Returns:
        True if relationship was created, False if it already existed.
    """
    if rel_type not in config.GRAPH_RELATIONSHIP_TYPES:
        logger.warning(f"Unknown relationship type: {rel_type}")
        return False
    if source_id == target_id:
        return False

    props = properties or {}
    props["created_at"] = datetime.utcnow().isoformat()

    # Use MERGE to be idempotent; SET only on CREATE to preserve existing props.
    # Dynamic rel types require APOC or per-type queries. We use per-type for safety.
    cypher = (
        f"MATCH (s:Artifact {{id: $source_id}}), (t:Artifact {{id: $target_id}}) "
        f"MERGE (s)-[r:{rel_type}]->(t) "
        f"ON CREATE SET r += $props "
        f"RETURN r IS NOT NULL AS ok, r.created_at = $created_at AS is_new"
    )
    with driver.session() as session:
        result = session.run(
            cypher,
            source_id=source_id,
            target_id=target_id,
            props=props,
            created_at=props["created_at"],
        )
        record = result.single()
        if record and record["is_new"]:
            logger.debug(
                f"Created {rel_type}: {source_id[:8]}→{target_id[:8]}"
            )
            return True
        return False


def find_related_artifacts(
    driver,
    artifact_ids: List[str],
    depth: int = 0,
    max_results: int = 0,
    rel_types: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Traverse the knowledge graph from the given artifacts and return
    related artifacts up to `depth` hops away.

    Args:
        driver: Neo4j driver
        artifact_ids: Starting artifact IDs
        depth: Max traversal hops (default from config)
        max_results: Max related artifacts returned (default from config)
        rel_types: Filter to these relationship types (default: all)

    Returns:
        List of related artifact dicts, each with:
            id, filename, domain, summary, keywords,
            relationship_type, relationship_depth, relationship_reason
    """
    if not artifact_ids:
        return []

    depth = depth or config.GRAPH_TRAVERSAL_DEPTH
    max_results = max_results or config.GRAPH_MAX_RELATED

    # Build relationship type filter
    if rel_types:
        valid = [r for r in rel_types if r in config.GRAPH_RELATIONSHIP_TYPES]
        if not valid:
            return []
        rel_filter = "|".join(valid)
    else:
        rel_filter = "|".join(config.GRAPH_RELATIONSHIP_TYPES)

    cypher = (
        f"MATCH path = (start:Artifact)-[:{rel_filter}*1..{depth}]-(related:Artifact) "
        f"WHERE start.id IN $artifact_ids AND NOT related.id IN $artifact_ids "
        f"WITH related, relationships(path) AS rels, length(path) AS hops "
        f"ORDER BY hops ASC "
        f"WITH related, head(collect(rels)) AS best_rels, min(hops) AS hops "
        f"RETURN related.id AS id, related.filename AS filename, "
        f"  related.domain AS domain, related.summary AS summary, "
        f"  related.keywords AS keywords, related.chunk_ids AS chunk_ids, "
        f"  related.chunk_count AS chunk_count, "
        f"  type(head(best_rels)) AS relationship_type, "
        f"  hops AS relationship_depth, "
        f"  head(best_rels).reason AS relationship_reason "
        f"ORDER BY hops ASC "
        f"LIMIT $max_results"
    )

    with driver.session() as session:
        result = session.run(
            cypher,
            artifact_ids=artifact_ids,
            max_results=max_results,
        )
        return [
            {
                "id": record["id"],
                "filename": record["filename"],
                "domain": record["domain"],
                "summary": record["summary"],
                "keywords": record["keywords"],
                "chunk_ids": record["chunk_ids"],
                "chunk_count": record["chunk_count"],
                "relationship_type": record["relationship_type"],
                "relationship_depth": record["relationship_depth"],
                "relationship_reason": record["relationship_reason"],
            }
            for record in result
        ]


# ---------------------------------------------------------------------------
# Relationship discovery (called during ingestion)
# ---------------------------------------------------------------------------

def _parse_keywords(keywords_json: str) -> Set[str]:
    """Safely parse a JSON keyword list into a lowercase set."""
    try:
        kw = json.loads(keywords_json) if keywords_json else []
        return {k.lower().strip() for k in kw if isinstance(k, str) and k.strip()}
    except (json.JSONDecodeError, TypeError):
        return set()


def _extract_references(content: str, filename: str) -> Set[str]:
    """
    Extract filenames referenced in content via import statements or
    explicit file mentions.
    """
    refs: Set[str] = set()

    # Python imports: `import foo`, `from foo import bar` (line-start only to avoid prose matches)
    for match in re.finditer(r'^\s*(?:from|import)\s+([\w.]+)', content, re.MULTILINE):
        module = match.group(1)
        # Convert module path to potential filename
        parts = module.split(".")
        refs.add(parts[-1] + ".py")
        if len(parts) > 1:
            refs.add(os.path.join(*parts) + ".py")

    # JS/TS imports: `require('...')`, `import ... from '...'`
    for match in re.finditer(r'''(?:require|from)\s*\(?['"]([^'"]+)['"]''', content):
        ref = match.group(1)
        if not ref.startswith(".") and "/" not in ref:
            continue  # skip bare module specifiers (npm packages)
        basename = os.path.basename(ref)
        if basename:
            refs.add(basename)
            # Add with common extensions
            if "." not in basename:
                refs.update([basename + ext for ext in (".js", ".ts", ".tsx", ".jsx")])

    # Explicit file mentions: any word ending with a known extension
    for ext in (".py", ".js", ".ts", ".md", ".pdf", ".yaml", ".json", ".csv"):
        for match in re.finditer(rf'\b([\w./-]+{re.escape(ext)})\b', content):
            ref = match.group(1)
            if ref != filename:
                refs.add(os.path.basename(ref))

    refs.discard(filename)  # don't reference self
    return refs


def discover_relationships(
    driver,
    artifact_id: str,
    filename: str,
    domain: str,
    keywords_json: str,
    content: str = "",
) -> int:
    """
    Discover and create relationships between a newly ingested artifact
    and existing artifacts. Called after successful ingestion.

    Strategies:
        1. Same-directory proximity — artifacts from the same parent directory
        2. Keyword overlap — artifacts sharing >= GRAPH_MIN_KEYWORD_OVERLAP keywords
        3. Content references — import statements or filename mentions

    Args:
        driver: Neo4j driver
        artifact_id: The newly ingested artifact's ID
        filename: Filename of the new artifact
        domain: Domain of the new artifact
        keywords_json: JSON array of keywords
        content: Full parsed text (for reference extraction)

    Returns:
        Number of relationships created.
    """
    created = 0
    new_keywords = _parse_keywords(keywords_json)

    # --- Strategy 1: Same-directory proximity ---
    # Artifacts with the same parent directory in their filename path
    parent_dir = os.path.dirname(filename)
    if parent_dir and parent_dir != ".":
        with driver.session() as session:
            result = session.run(
                "MATCH (a:Artifact) "
                "WHERE a.id <> $artifact_id AND a.domain = $domain "
                "AND a.filename STARTS WITH $parent_prefix "
                "RETURN a.id AS id "
                "LIMIT 10",
                artifact_id=artifact_id,
                domain=domain,
                parent_prefix=parent_dir + "/",
            )
            for record in result:
                if create_relationship(
                    driver,
                    artifact_id,
                    record["id"],
                    "RELATES_TO",
                    {"reason": f"same directory: {parent_dir}"},
                ):
                    created += 1

    # --- Strategy 2: Keyword overlap ---
    if len(new_keywords) >= config.GRAPH_MIN_KEYWORD_OVERLAP:
        with driver.session() as session:
            # Find artifacts that share keywords (within same or related domains)
            result = session.run(
                "MATCH (a:Artifact) "
                "WHERE a.id <> $artifact_id AND a.keywords IS NOT NULL "
                "RETURN a.id AS id, a.keywords AS keywords "
                "ORDER BY a.ingested_at DESC "
                "LIMIT 200",
                artifact_id=artifact_id,
            )
            for record in result:
                other_keywords = _parse_keywords(record["keywords"])
                overlap = new_keywords & other_keywords
                if len(overlap) >= config.GRAPH_MIN_KEYWORD_OVERLAP:
                    if create_relationship(
                        driver,
                        artifact_id,
                        record["id"],
                        "RELATES_TO",
                        {
                            "reason": f"shared keywords: {', '.join(sorted(overlap)[:5])}",
                            "overlap_count": len(overlap),
                        },
                    ):
                        created += 1

    # --- Strategy 3: Content references (imports, file mentions) ---
    if content:
        refs = _extract_references(content, filename)
        if refs:
            with driver.session() as session:
                for ref_name in refs:
                    result = session.run(
                        "MATCH (a:Artifact) "
                        "WHERE a.id <> $artifact_id AND a.filename ENDS WITH $ref_name "
                        "RETURN a.id AS id, a.filename AS filename "
                        "LIMIT 3",
                        artifact_id=artifact_id,
                        ref_name=ref_name,
                    )
                    for record in result:
                        if create_relationship(
                            driver,
                            artifact_id,
                            record["id"],
                            "REFERENCES",
                            {"reason": f"references {record['filename']}"},
                        ):
                            created += 1

    if created > 0:
        logger.info(
            f"Discovered {created} relationship(s) for artifact {artifact_id[:8]} ({filename})"
        )
    return created
