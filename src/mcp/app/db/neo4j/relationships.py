# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Neo4j relationship management and discovery."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import config
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.graph")


def create_relationship(
    driver,
    source_id: str,
    target_id: str,
    rel_type: str,
    properties: dict[str, Any] | None = None,
) -> bool:
    """Create a typed relationship between two artifacts. Idempotent."""
    if rel_type not in config.GRAPH_RELATIONSHIP_TYPES:
        logger.warning(f"Unknown relationship type: {rel_type}")
        return False
    if source_id == target_id:
        return False

    props = properties or {}
    props["created_at"] = utcnow_iso()

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
    artifact_ids: list[str],
    depth: int = 0,
    max_results: int = 0,
    rel_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Traverse the knowledge graph and return related artifacts up to `depth` hops away."""
    if not artifact_ids:
        return []

    # Hard-clamp to prevent injection via f-string interpolation in Cypher
    depth = max(1, min(int(depth or config.GRAPH_TRAVERSAL_DEPTH), 4))
    max_results = max(1, min(int(max_results or config.GRAPH_MAX_RELATED), 50))

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

def _parse_keywords(keywords_json: str) -> set[str]:
    """Safely parse a JSON keyword list into a lowercase set."""
    try:
        kw = json.loads(keywords_json) if keywords_json else []
        return {k.lower().strip() for k in kw if isinstance(k, str) and k.strip()}
    except (json.JSONDecodeError, TypeError):
        return set()


def _extract_references(content: str, filename: str) -> set[str]:
    """Extract filenames referenced in content via imports or explicit mentions."""
    refs: set[str] = set()

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


def _batch_merge_relationships(
    driver,
    source_id: str,
    edges: list[dict[str, Any]],
    rel_type: str,
) -> int:
    """MERGE ``rel_type`` edges from ``source_id`` to every edge's target in ONE
    UNWIND query (AF-090).

    Replaces the old per-edge ``create_relationship`` loop — ``discover_relationships``
    used to fire up to ~240 sequential single-edge MERGEs (each opening its own
    session) on the hot ingest path. ``edges`` is a list of
    ``{"target_id": str, "props": dict}``; it is deduped by ``target_id``
    (first-wins props) and self-edges are dropped, so the batch matches the old
    loop's first-wins idempotency exactly. Returns the count of NEWLY created
    edges — pre-existing edges are left untouched (same ``ON CREATE`` semantics as
    ``create_relationship``). Newness is measured by an ``OPTIONAL MATCH`` against
    the pre-MERGE graph state, so the count is exact regardless of timestamp
    resolution.
    """
    if rel_type not in config.GRAPH_RELATIONSHIP_TYPES:
        logger.warning(f"Unknown relationship type: {rel_type}")
        return 0
    seen: dict[str, dict[str, Any]] = {}
    for edge in edges:
        tid = edge.get("target_id")
        if tid and tid != source_id and tid not in seen:
            seen[tid] = edge.get("props") or {}
    if not seen:
        return 0

    payload = [{"target_id": tid, "props": props} for tid, props in seen.items()]
    created_at = utcnow_iso()
    # rel_type is validated against GRAPH_RELATIONSHIP_TYPES above (a fixed
    # allowlist, never user input), so its interpolation into the query is safe;
    # dynamic rel types would otherwise require APOC.
    cypher = (
        "UNWIND $edges AS edge "
        "MATCH (s:Artifact {id: $source_id}), (t:Artifact {id: edge.target_id}) "
        f"OPTIONAL MATCH (s)-[existing:{rel_type}]->(t) "
        f"MERGE (s)-[r:{rel_type}]->(t) "
        "ON CREATE SET r += edge.props, r.created_at = $created_at "
        "RETURN sum(CASE WHEN existing IS NULL THEN 1 ELSE 0 END) AS new_count"
    )
    with driver.session() as session:
        record = session.run(
            cypher,
            source_id=source_id,
            edges=payload,
            created_at=created_at,
        ).single()
    new_count = int(record["new_count"]) if record and record["new_count"] is not None else 0
    if new_count:
        logger.debug(
            "Batched %d new %s edge(s) from %s", new_count, rel_type, source_id[:8]
        )
    return new_count


def discover_relationships(
    driver,
    artifact_id: str,
    filename: str,
    domain: str,
    keywords_json: str,
    content: str = "",
) -> int:
    """Discover and create relationships between a newly ingested artifact and existing ones.

    AF-090: each strategy collects its candidate edges from a single read query,
    then writes them all in ONE batched UNWIND MERGE (``_batch_merge_relationships``)
    instead of a per-edge ``create_relationship`` loop.
    """
    created = 0
    new_keywords = _parse_keywords(keywords_json)

    # --- Strategy 1: Same-directory proximity ---
    # Artifacts with the same parent directory in their filename path
    parent_dir = os.path.dirname(filename)
    if parent_dir and parent_dir != ".":
        s1_edges: list[dict[str, Any]] = []
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
                s1_edges.append({
                    "target_id": record["id"],
                    "props": {"reason": f"same directory: {parent_dir}"},
                })
        created += _batch_merge_relationships(driver, artifact_id, s1_edges, "RELATES_TO")

    # --- Strategy 2: Keyword overlap ---
    if len(new_keywords) >= config.GRAPH_MIN_KEYWORD_OVERLAP:
        s2_edges: list[dict[str, Any]] = []
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
                    s2_edges.append({
                        "target_id": record["id"],
                        "props": {
                            "reason": f"shared keywords: {', '.join(sorted(overlap)[:5])}",
                            "overlap_count": len(overlap),
                        },
                    })
        created += _batch_merge_relationships(driver, artifact_id, s2_edges, "RELATES_TO")

    # --- Strategy 3: Content references (imports, file mentions) ---
    if content:
        refs = _extract_references(content, filename)
        if refs:
            s3_edges: list[dict[str, Any]] = []
            with driver.session() as session:
                # Batch all ref lookups in a single UNWIND query
                result = session.run(
                    "UNWIND $ref_names AS ref_name "
                    "MATCH (a:Artifact) "
                    "WHERE a.id <> $artifact_id AND a.filename ENDS WITH ref_name "
                    "RETURN a.id AS id, a.filename AS filename, ref_name "
                    "LIMIT 30",
                    artifact_id=artifact_id,
                    ref_names=list(refs),
                )
                for record in result:
                    s3_edges.append({
                        "target_id": record["id"],
                        "props": {"reason": f"references {record['filename']}"},
                    })
            created += _batch_merge_relationships(driver, artifact_id, s3_edges, "REFERENCES")

    if created > 0:
        logger.info(
            f"Discovered {created} relationship(s) for artifact {artifact_id[:8]} ({filename})"
        )
    return created


# ---------------------------------------------------------------------------
# RAG Cycle C2.4 — email attachment edges
# ---------------------------------------------------------------------------

def write_has_attachment(
    driver,
    parent_id: str,
    child_id: str,
    filename: str,
    content_type: str,
) -> bool:
    """Write a ``(:Artifact)-[:HAS_ATTACHMENT]->(:Artifact)`` edge.

    Parent is the email artifact; child is the artifact extracted from
    one of its attachments. Properties on the edge:

    * ``filename`` — the attachment's original filename
    * ``content_type`` — MIME type from the email part
    * ``attached_at`` — wall-clock ISO timestamp of the edge write

    Idempotent: MERGE keyed on ``(parent_id, child_id)`` so re-ingesting
    the parent (e.g. through ``_reingest_artifact``) does not duplicate
    the edge. ``ON CREATE`` writes the metadata; existing edges are left
    untouched (the first attachment metadata wins, matching how the
    discovery-graph creates ``RELATES_TO`` edges).

    Returns ``True`` if a new edge was created, ``False`` if it already
    existed (or the MATCH failed because one of the nodes is missing).
    Best-effort logging on errors — the caller wraps this in a
    ``log_swallowed_error`` boundary because attachment-edge creation
    must never break ingestion.
    """
    if parent_id == child_id:
        # Defensive: a content_hash dedup collapse from an email onto its
        # own attachment is a degenerate case. Skip silently.
        return False

    now = utcnow_iso()
    cypher = (
        "MATCH (p:Artifact {id: $parent_id}), (c:Artifact {id: $child_id}) "
        "MERGE (p)-[r:HAS_ATTACHMENT]->(c) "
        "ON CREATE SET r.filename = $filename, "
        "              r.content_type = $content_type, "
        "              r.attached_at = $now "
        "RETURN r.attached_at = $now AS is_new"
    )
    with driver.session() as session:
        record = session.run(
            cypher,
            parent_id=parent_id,
            child_id=child_id,
            filename=filename,
            content_type=content_type,
            now=now,
        ).single()
        is_new = bool(record and record["is_new"])
        if is_new:
            logger.debug(
                "HAS_ATTACHMENT: %s -[%s]-> %s",
                parent_id[:8], filename, child_id[:8],
            )
        return is_new
