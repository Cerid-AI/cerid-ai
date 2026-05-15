# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase 4 graph-native tools — 4 tools.

The Neo4j graph has been accumulating `:Artifact` + relationship data
since v0.83. These tools surface that latent value via the MCP
interface so LLMs can reason about connection structure, not just
chunk content. GDS 2026.04.0 is enabled (`NEO4J_PLUGINS` includes
``graph-data-science``) so the community-detection tool runs at
production speed.

* ``pkb_graph_neighbors`` — k-hop neighbourhood of one artifact.
* ``pkb_graph_path`` — shortest path between two artifacts.
* ``pkb_graph_communities`` — community detection via GDS Louvain.
* ``pkb_concept_evolution`` — temporal mentions of a concept.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import config
from app.deps import get_neo4j
from app.tool_registry import (
    InvalidParamsError,
    ResourceNotFoundError,
    UpstreamUnavailableError,
    register_tool,
)

logger = logging.getLogger("ai-companion.mcp_tools.graph")


def _parse_period_to_since(period: str) -> str:
    """Parse a period string (e.g. '30d', '24h', '4w') into an ISO-8601
    cutoff usable in Cypher queries.
    """
    units = {"h": "hours", "d": "days", "w": "weeks"}
    suffix = period[-1].lower() if period else ""
    if suffix not in units:
        raise InvalidParamsError(
            f"Invalid period {period!r}; expected like '24h', '7d', '4w'"
        )
    try:
        n = int(period[:-1])
    except ValueError as e:
        raise InvalidParamsError(f"Invalid period {period!r}") from e
    delta = timedelta(**{units[suffix]: n})
    return (datetime.now(timezone.utc) - delta).isoformat()


# ============================================================ pkb_graph_neighbors


@register_tool(
    name="pkb_graph_neighbors",
    description=(
        "Return k-hop neighbours of an artifact along configurable "
        "relationship types. **Use when** the user asks 'what's "
        "related to this?' — surfaces graph connections invisible to "
        "pure-similarity retrieval. **Returns** `{neighbors: "
        "[{artifact_id, distance, relationship_path, filename, "
        "domain}], total}`. Capped at `limit` (default 50). Empty "
        "`relationship_types` walks any edge."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string"},
            "depth": {
                "type": "integer",
                "description": "Maximum hops (1-4)",
                "default": 2,
            },
            "relationship_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Edge types to walk (e.g. ['MENTIONS', 'RELATES_TO']). Empty = all.",
            },
            "limit": {"type": "integer", "default": 50},
        },
        "required": ["artifact_id"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "neighbors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "artifact_id": {"type": "string"},
                        "distance": {"type": "integer"},
                        "relationship_path": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "filename": {"type": "string"},
                        "domain": {"type": "string"},
                    },
                },
            },
            "total": {"type": "integer"},
            "source_artifact_id": {"type": "string"},
        },
    },
    cost_class="medium",
)
async def pkb_graph_neighbors(
    artifact_id: str,
    depth: int = 2,
    relationship_types: list[str] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    depth = max(1, min(int(depth), 4))
    limit = max(1, min(int(limit), 200))
    rel_types = relationship_types or []

    # Build relationship filter — empty list = walk anything.
    if rel_types:
        rel_pattern = ":" + "|".join(rel_types)
    else:
        rel_pattern = ""

    driver = get_neo4j()

    def _run() -> list[dict[str, Any]]:
        with driver.session() as session:
            # First confirm the source exists; -32004 if not.
            check = session.run(
                "MATCH (a:Artifact {id: $id}) RETURN count(a) AS c",
                id=artifact_id,
            )
            if int(check.single()["c"]) == 0:
                return []
            # Variable-length path query. We cap depth in the query.
            result = session.run(
                f"""
                MATCH path = (start:Artifact {{id: $id}})
                  -[{rel_pattern}*1..{depth}]-(n:Artifact)
                WHERE n.id <> $id
                  AND coalesce(n.archived, false) = false
                WITH n, length(path) AS distance, path,
                     [r in relationships(path) | type(r)] AS rel_path
                RETURN DISTINCT
                    n.id AS artifact_id,
                    distance,
                    rel_path,
                    n.filename AS filename,
                    n.domain AS domain
                ORDER BY distance, artifact_id
                LIMIT $limit
                """,
                id=artifact_id,
                limit=limit,
            )
            return [dict(r) for r in result]

    try:
        rows = await asyncio.to_thread(_run)
    except Exception as exc:
        raise UpstreamUnavailableError(f"Neo4j unreachable: {exc}") from exc

    if not rows:
        # Either source missing or zero matching neighbours. Disambiguate.
        def _check() -> bool:
            with driver.session() as session:
                r = session.run(
                    "MATCH (a:Artifact {id: $id}) RETURN count(a) AS c", id=artifact_id,
                )
                return int(r.single()["c"]) > 0

        exists = await asyncio.to_thread(_check)
        if not exists:
            raise ResourceNotFoundError(f"Artifact {artifact_id!r} not found")

    return {
        "neighbors": [
            {
                "artifact_id": r["artifact_id"],
                "distance": int(r["distance"]),
                "relationship_path": list(r["rel_path"] or []),
                "filename": r.get("filename") or "",
                "domain": r.get("domain") or "",
            }
            for r in rows
        ],
        "total": len(rows),
        "source_artifact_id": artifact_id,
    }


# ============================================================ pkb_graph_path


@register_tool(
    name="pkb_graph_path",
    description=(
        "Find shortest path(s) between two artifacts in the relationship "
        "graph. Uses GDS Dijkstra (unweighted; every edge has cost 1). "
        "**Use when** the user asks 'how are X and Y connected?' — "
        "surfaces multi-hop connections retrieval can't see. "
        "**Returns** `{paths: [{nodes: [{id, filename}], edges: "
        "[{type}], length: N}], from_id, to_id}`. Empty paths array "
        "when no connection exists within max_depth."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "from_id": {"type": "string"},
            "to_id": {"type": "string"},
            "max_depth": {
                "type": "integer",
                "description": "Maximum hops to search (1-6)",
                "default": 4,
            },
        },
        "required": ["from_id", "to_id"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "nodes": {"type": "array", "items": {"type": "object"}},
                        "edges": {"type": "array", "items": {"type": "object"}},
                        "length": {"type": "integer"},
                    },
                },
            },
            "from_id": {"type": "string"},
            "to_id": {"type": "string"},
        },
    },
    cost_class="medium",
)
async def pkb_graph_path(
    from_id: str,
    to_id: str,
    max_depth: int = 4,
) -> dict[str, Any]:
    if from_id == to_id:
        raise InvalidParamsError("from_id and to_id must differ")
    max_depth = max(1, min(int(max_depth), 6))
    driver = get_neo4j()

    def _run() -> list[dict[str, Any]]:
        with driver.session() as session:
            # Validate both endpoints exist first; -32004 if missing.
            check = session.run(
                "MATCH (a:Artifact) WHERE a.id IN [$f, $t] "
                "RETURN collect(a.id) AS found",
                f=from_id, t=to_id,
            )
            found = list(check.single()["found"] or [])
            missing = {from_id, to_id} - set(found)
            if missing:
                raise ResourceNotFoundError(
                    f"Artifact(s) not found: {sorted(missing)!r}"
                )

            # Native shortestPath function — fast, no GDS projection
            # needed. Limit to length max_depth.
            result = session.run(
                f"""
                MATCH (a:Artifact {{id: $f}}), (b:Artifact {{id: $t}})
                MATCH path = shortestPath((a)-[*..{max_depth}]-(b))
                WITH path, length(path) AS len
                RETURN [n in nodes(path) | {{id: n.id, filename: coalesce(n.filename, ''),
                                            label: labels(n)[0]}}] AS nodes,
                       [r in relationships(path) | {{type: type(r)}}] AS edges,
                       len AS length
                """,
                f=from_id, t=to_id,
            )
            return [dict(r) for r in result]

    try:
        rows = await asyncio.to_thread(_run)
    except ResourceNotFoundError:
        raise
    except Exception as exc:
        raise UpstreamUnavailableError(f"Neo4j unreachable: {exc}") from exc

    paths = [
        {
            "nodes": list(r["nodes"] or []),
            "edges": list(r["edges"] or []),
            "length": int(r["length"]),
        }
        for r in rows
    ]
    return {"paths": paths, "from_id": from_id, "to_id": to_id}


# ============================================================ pkb_graph_communities


@register_tool(
    name="pkb_graph_communities",
    description=(
        "Run Louvain community detection over the artifact-relationship "
        "graph. Requires the GDS plugin (already enabled in cerid's "
        "Neo4j). **Use when** mapping the KB's macro-structure — what "
        "clusters of related content exist and what they're about. "
        "**Returns** `{communities: [{community_id, size, "
        "artifact_ids, top_filenames, dominant_domain}], "
        "graph_size}`. Filtered to communities >= min_size."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": (
                    "Restrict to one domain ("
                    f"{', '.join(config.DOMAINS)}). Empty = all domains."
                ),
                "default": "",
            },
            "min_size": {
                "type": "integer",
                "description": "Minimum members per reported community",
                "default": 3,
            },
            "max_communities": {
                "type": "integer",
                "description": "Cap on communities returned (default 20)",
                "default": 20,
            },
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "communities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "community_id": {"type": "integer"},
                        "size": {"type": "integer"},
                        "artifact_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "top_filenames": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "dominant_domain": {"type": "string"},
                    },
                },
            },
            "graph_size": {"type": "integer"},
            "domain_filter": {"type": "string"},
        },
    },
    cost_class="high",
)
async def pkb_graph_communities(
    domain: str = "",
    min_size: int = 3,
    max_communities: int = 20,
) -> dict[str, Any]:
    if domain and domain not in config.DOMAINS:
        raise InvalidParamsError(
            f"Invalid domain {domain!r}. Valid: {sorted(config.DOMAINS)}"
        )
    min_size = max(2, int(min_size))
    max_communities = max(1, min(int(max_communities), 100))

    domain_filter = "WHERE a.domain = $domain" if domain else ""
    driver = get_neo4j()

    def _run() -> dict[str, Any]:
        # Use GDS cypher projection so we can scope to one domain
        # without polluting the catalog with named graphs. Anonymous
        # projection auto-cleans after the call.
        graph_name = f"_pkb_communities_{abs(hash(domain or 'all')) % 10**8}"

        with driver.session() as session:
            # Drop any leftover projection from a prior aborted call.
            try:
                session.run(
                    "CALL gds.graph.drop($name, false) YIELD graphName RETURN graphName",
                    name=graph_name,
                ).consume()
            except Exception:
                pass

            # Project + run Louvain. Native projection on (:Artifact)
            # with the connection relationships. We treat all edges
            # as undirected for community detection.
            session.run(
                """
                CALL gds.graph.project.cypher(
                    $name,
                    $node_query,
                    $rel_query,
                    {validateRelationships: false}
                ) YIELD graphName
                RETURN graphName
                """,
                name=graph_name,
                node_query=(
                    "MATCH (a:Artifact) "
                    + domain_filter
                    + " AND coalesce(a.archived, false) = false "
                    if domain
                    else
                    "MATCH (a:Artifact) WHERE coalesce(a.archived, false) = false "
                ) + "RETURN id(a) AS id, a.id AS artifact_id, a.filename AS filename, a.domain AS domain",
                rel_query=(
                    "MATCH (a:Artifact)-[r]-(b:Artifact) "
                    + (
                        "WHERE a.domain = $domain AND b.domain = $domain "
                        if domain else ""
                    )
                    + "AND coalesce(a.archived, false) = false "
                    + "AND coalesce(b.archived, false) = false "
                    + "RETURN id(a) AS source, id(b) AS target, type(r) AS type"
                ),
                domain=domain or "",
            ).consume()

            # Graph size sanity check
            size_row = session.run(
                "CALL gds.graph.list($name) YIELD graphName, nodeCount RETURN nodeCount",
                name=graph_name,
            ).single()
            node_count = int(size_row["nodeCount"]) if size_row else 0

            if node_count == 0:
                session.run("CALL gds.graph.drop($name, false)", name=graph_name).consume()
                return {"communities": [], "graph_size": 0}

            # Run Louvain
            result = session.run(
                """
                CALL gds.louvain.stream($name) YIELD nodeId, communityId
                WITH communityId, collect(nodeId) AS member_ids, count(*) AS size
                WHERE size >= $min_size
                RETURN communityId AS community_id, size, member_ids
                ORDER BY size DESC
                LIMIT $max_communities
                """,
                name=graph_name,
                min_size=min_size,
                max_communities=max_communities,
            )

            communities_raw = [dict(r) for r in result]

            # Enrich each community with artifact IDs, filenames, dominant domain.
            communities: list[dict[str, Any]] = []
            for c in communities_raw:
                member_ids = c["member_ids"]
                enrich = session.run(
                    """
                    UNWIND $ids AS nid
                    MATCH (a:Artifact) WHERE id(a) = nid
                    RETURN a.id AS artifact_id, a.filename AS filename, a.domain AS domain
                    LIMIT 200
                    """,
                    ids=member_ids,
                )
                rows = [dict(r) for r in enrich]
                domains_count: dict[str, int] = {}
                for r in rows:
                    d = r.get("domain") or ""
                    domains_count[d] = domains_count.get(d, 0) + 1
                dominant = max(domains_count.items(), key=lambda x: x[1])[0] if domains_count else ""
                communities.append({
                    "community_id": int(c["community_id"]),
                    "size": int(c["size"]),
                    "artifact_ids": [r["artifact_id"] for r in rows][:50],
                    "top_filenames": [r["filename"] for r in rows if r.get("filename")][:10],
                    "dominant_domain": dominant,
                })

            # Cleanup projection
            session.run("CALL gds.graph.drop($name, false)", name=graph_name).consume()
            return {"communities": communities, "graph_size": node_count}

    try:
        out = await asyncio.to_thread(_run)
    except Exception as exc:
        raise UpstreamUnavailableError(f"GDS Louvain failed: {exc}") from exc

    out["domain_filter"] = domain
    return out


# ============================================================ pkb_concept_evolution


@register_tool(
    name="pkb_concept_evolution",
    description=(
        "Show how mentions of a concept evolved over time, plus the "
        "top co-mentioned concepts in each window. **Use when** "
        "tracking the trajectory of a topic (e.g. 'how has my use of "
        "X changed over 30 days?'). **Returns** `{timeline: [{date, "
        "mention_count, top_co_concepts: [str]}], concept, period, "
        "granularity}`. Granularity ∈ {day, week, month}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "concept": {
                "type": "string",
                "description": (
                    "Concept to track. Matched against entity canonical_id, "
                    "entity name, or artifact title substring."
                ),
            },
            "period": {
                "type": "string",
                "description": "Lookback (e.g. '30d', '90d', '1y' — actually '52w')",
                "default": "30d",
            },
            "granularity": {
                "type": "string",
                "enum": ["day", "week", "month"],
                "default": "day",
            },
        },
        "required": ["concept"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "timeline": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string"},
                        "mention_count": {"type": "integer"},
                        "top_co_concepts": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
            },
            "concept": {"type": "string"},
            "period": {"type": "string"},
            "granularity": {"type": "string"},
            "total_mentions": {"type": "integer"},
        },
    },
    cost_class="medium",
)
async def pkb_concept_evolution(
    concept: str,
    period: str = "30d",
    granularity: str = "day",
) -> dict[str, Any]:
    if not concept.strip():
        raise InvalidParamsError("concept must be non-empty")
    if granularity not in ("day", "week", "month"):
        raise InvalidParamsError(
            f"granularity must be day/week/month; got {granularity!r}"
        )
    since_iso = _parse_period_to_since(period)

    # Truncation expression for the date grouping
    _trunc = {"day": "day", "week": "week", "month": "month"}[granularity]

    driver = get_neo4j()

    def _run() -> list[dict[str, Any]]:
        with driver.session() as session:
            # Find artifacts that mention the concept (entity name or
            # canonical_id, or filename/keyword substring as a fallback).
            result = session.run(
                f"""
                MATCH (a:Artifact)
                WHERE a.ingested_at >= $since
                  AND coalesce(a.archived, false) = false
                  AND (
                    toLower(coalesce(a.filename, '')) CONTAINS toLower($concept)
                    OR toLower(coalesce(a.keywords, '')) CONTAINS toLower($concept)
                    OR EXISTS {{
                        (a)-[:MENTIONS]->(e:Entity)
                        WHERE toLower(e.name) = toLower($concept)
                           OR toLower(e.canonical_id) = toLower($concept)
                    }}
                  )
                WITH a, date(datetime(a.ingested_at)) AS d
                WITH date.truncate('{_trunc}', d) AS bucket, collect(a) AS artifacts_in_bucket
                ORDER BY bucket
                RETURN
                    toString(bucket) AS date,
                    size(artifacts_in_bucket) AS mention_count,
                    [a in artifacts_in_bucket |
                        [
                            (a)-[:MENTIONS]->(e2:Entity)
                            WHERE toLower(e2.name) <> toLower($concept)
                              AND toLower(e2.canonical_id) <> toLower($concept)
                            | e2.name
                        ]
                    ] AS co_concept_lists
                """,
                since=since_iso, concept=concept,
            )
            rows = []
            for r in result:
                # Flatten + count co-concepts to find top 5 in this bucket.
                co_lists = r["co_concept_lists"] or []
                co_counts: dict[str, int] = {}
                for lst in co_lists:
                    for c in (lst or []):
                        if c:
                            co_counts[c] = co_counts.get(c, 0) + 1
                top = sorted(co_counts.items(), key=lambda x: -x[1])[:5]
                rows.append({
                    "date": r["date"],
                    "mention_count": int(r["mention_count"]),
                    "top_co_concepts": [c for c, _ in top],
                })
            return rows

    try:
        timeline = await asyncio.to_thread(_run)
    except Exception as exc:
        raise UpstreamUnavailableError(f"Neo4j query failed: {exc}") from exc

    return {
        "timeline": timeline,
        "concept": concept,
        "period": period,
        "granularity": granularity,
        "total_mentions": sum(t["mention_count"] for t in timeline),
    }
