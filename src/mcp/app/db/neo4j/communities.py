# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Neo4j data-access for the Leiden community explorer (Phase R.2).

Queries the ``(:Community)`` nodes produced by Phase 4b and surfaces
them to the observability API.

Existing Community node shape (from community_detection.py + community_summaries.py):

    (:Community {
        id,                     # "{level}:{native_id}"  (unique constraint)
        level,                  # int — Leiden hierarchy depth; 0 = finest
        native_id,              # int — GDS-assigned community id
        summary,                # str | None — LLM-generated theme summary
        summary_generated_at,   # ISO str | None — when summary was written
        top_terms,              # list[str] | None — c-TF-IDF fallback labels (compute_umap_3d A3)
        created_at,             # ISO str
        updated_at,             # ISO str
    })

Relationship:
    (:Entity)-[:IN_COMMUNITY]->(:Community)
    (:Entity)-[:CO_MENTIONED]->(:Entity {weight: int})

Pydantic models live here (not in the service) so the adapter is
self-contained.  The service imports the models and delegates all
I/O to this module.

Callers: :mod:`app.services.community_pages` only.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.graph.communities")


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


class CommunitySummary(BaseModel):
    """Lightweight summary row for the communities list endpoint.

    ``last_summarized_at`` mirrors the Community node's
    ``summary_generated_at`` property.
    """

    community_id: str
    level: int
    summary: str | None = None
    member_count: int
    last_summarized_at: str | None = None


class MemberEntity(BaseModel):
    """Minimal entity reference inside a community detail."""

    canonical_id: str
    name: str
    entity_type: str


class RelatedCommunity(BaseModel):
    """Another community that frequently co-occurs with this one."""

    community_id: str
    co_mention_count: int


class CommunityFull(CommunitySummary):
    """Full community page: adds member entities and related communities."""

    members: list[MemberEntity] = []
    related_communities: list[RelatedCommunity] = []


# ---------------------------------------------------------------------------
# list_communities
# ---------------------------------------------------------------------------


def list_communities(
    driver: Any,
    *,
    min_size: int = 3,
    limit: int = 30,
    level: int = 0,
) -> list[CommunitySummary]:
    """Return communities ordered by member_count descending.

    Filters to ``level`` (default 0 = finest granularity) and discards
    tiny communities (< ``min_size`` members).  Only communities that
    have a cached ``summary`` are returned (same guard as the existing
    ``list_community_summaries`` read-back helper in community_summaries.py).

    Args:
        driver: live Neo4j driver.
        min_size: minimum member count (default 3).
        limit: max rows returned (default 30, cap 200).
        level: Leiden hierarchy depth to query (default 0).

    Returns:
        Sorted list of CommunitySummary Pydantic models.
    """
    effective_limit = min(limit, 200)
    cypher = """
    MATCH (c:Community {level: $level})
    WHERE c.summary IS NOT NULL
    OPTIONAL MATCH (c)<-[:IN_COMMUNITY]-(e:Entity)
    WITH c, count(DISTINCT e) AS member_count
    WHERE member_count >= $min_size
    RETURN
        c.id                   AS community_id,
        c.level                AS level,
        c.summary              AS summary,
        member_count,
        c.summary_generated_at AS last_summarized_at
    ORDER BY member_count DESC
    LIMIT $limit
    """
    try:
        with driver.session() as session:
            rows = session.run(
                cypher,
                level=level,
                min_size=min_size,
                limit=effective_limit,
            )
            result = []
            for row in rows:
                r = dict(row)
                try:
                    result.append(
                        CommunitySummary(
                            community_id=str(r.get("community_id", "")),
                            level=int(r.get("level", 0)),
                            summary=r.get("summary"),
                            member_count=int(r.get("member_count", 0)),
                            last_summarized_at=r.get("last_summarized_at"),
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    log_swallowed_error(
                        "communities.list_communities.row_parse",
                        exc,
                        context={"row": r},
                    )
            return result
    except Exception as exc:
        log_swallowed_error("communities.list_communities", exc)
        raise


# ---------------------------------------------------------------------------
# get_community
# ---------------------------------------------------------------------------


def get_community(driver: Any, community_id: str) -> CommunityFull | None:
    """Fetch the full community record.

    Returns ``None`` when no Community with the given id exists.

    Assembly:
    1. Core community node (summary, member_count, level, etc.).
    2. Member entity list (canonical_id, name, entity_type).
    3. Related communities — top-K communities that share the most
       CO_MENTIONED edges with members of this community.  Because
       CO_MENTIONED is between entities we use the following pattern:

       (member of community A)-[:CO_MENTIONED]->(peer entity)-[:IN_COMMUNITY]
           ->(community B ≠ A)

       The edge weight field on CO_MENTIONED is summed as a co-mention
       count proxy.  The top-5 other communities by that sum are returned.

    Args:
        driver: live Neo4j driver.
        community_id: value of Community.id ("{level}:{native_id}").

    Returns:
        CommunityFull | None.
    """
    try:
        with driver.session() as session:
            # --- 1. Core node -----------------------------------------------
            core_result = session.run(
                """
                MATCH (c:Community {id: $cid})
                OPTIONAL MATCH (c)<-[:IN_COMMUNITY]-(e:Entity)
                WITH c, count(DISTINCT e) AS member_count
                RETURN
                    c.id                   AS community_id,
                    c.level                AS level,
                    c.summary              AS summary,
                    member_count,
                    c.summary_generated_at AS last_summarized_at
                LIMIT 1
                """,
                cid=community_id,
            )
            core_row = core_result.single()
            if core_row is None:
                return None
            core = dict(core_row)

            # --- 2. Members -------------------------------------------------
            members_result = session.run(
                """
                MATCH (c:Community {id: $cid})<-[:IN_COMMUNITY]-(e:Entity)
                RETURN
                    e.canonical_id AS canonical_id,
                    e.name         AS name,
                    e.entity_type  AS entity_type
                ORDER BY coalesce(e.mention_count, 0) DESC
                LIMIT 50
                """,
                cid=community_id,
            )
            members = [
                MemberEntity(
                    canonical_id=str(r["canonical_id"] or ""),
                    name=str(r["name"] or ""),
                    entity_type=str(r["entity_type"] or "OTHER"),
                )
                for r in members_result
            ]

            # --- 3. Related communities (co-mention bridge) -----------------
            related_result = session.run(
                """
                MATCH (c:Community {id: $cid})<-[:IN_COMMUNITY]-(e:Entity)
                MATCH (e)-[cm:CO_MENTIONED]->(peer:Entity)
                MATCH (peer)-[:IN_COMMUNITY]->(other:Community)
                WHERE other.id <> $cid
                WITH other.id AS other_id, sum(coalesce(cm.weight, 1)) AS co_count
                ORDER BY co_count DESC
                LIMIT 5
                RETURN other_id AS community_id, co_count AS co_mention_count
                """,
                cid=community_id,
            )
            related = [
                RelatedCommunity(
                    community_id=str(r["community_id"]),
                    co_mention_count=int(r["co_mention_count"]),
                )
                for r in related_result
            ]

        return CommunityFull(
            community_id=str(core.get("community_id", "")),
            level=int(core.get("level", 0)),
            summary=core.get("summary"),
            member_count=int(core.get("member_count", 0)),
            last_summarized_at=core.get("last_summarized_at"),
            members=members,
            related_communities=related,
        )

    except Exception as exc:
        log_swallowed_error(
            "communities.get_community", exc, context={"community_id": community_id}
        )
        raise


# ---------------------------------------------------------------------------
# community_hierarchy
# ---------------------------------------------------------------------------


class CommunityHierarchyNode(BaseModel):
    community_id: str
    level: int
    parent_id: str | None = None
    member_count: int
    summary: str | None = None
    top_terms: list[str] | None = None


class CommunityHierarchy(BaseModel):
    levels: int
    nodes: list[CommunityHierarchyNode] = []


def community_hierarchy(
    driver: Any,
    *,
    max_levels: int = 5,
    min_size: int = 1,
) -> CommunityHierarchy:
    """Return the full Leiden community hierarchy across all levels.

    parent_id of a level-L community is the level-(L+1) community that its
    members belong to (derived from Entity.leiden_communityIds, index = level).
    Top-level communities have parent_id == None.
    """
    cypher = """
    MATCH (c:Community)
    WHERE c.level < $max_levels
    OPTIONAL MATCH (c)<-[:IN_COMMUNITY]-(e:Entity)
    WITH c, count(DISTINCT e) AS member_count, collect(e)[0] AS sample
    WHERE member_count >= $min_size
    WITH c, member_count, sample,
         CASE
           WHEN sample IS NOT NULL
            AND sample.leiden_communityIds IS NOT NULL
            AND size(sample.leiden_communityIds) > c.level + 1
           THEN (c.level + 1) + ':' + toString(sample.leiden_communityIds[c.level + 1])
           ELSE null
         END AS parent_id
    RETURN
        c.id        AS community_id,
        c.level     AS level,
        parent_id   AS parent_id,
        member_count,
        c.summary   AS summary,
        c.top_terms AS top_terms
    ORDER BY c.level ASC, member_count DESC
    """
    try:
        with driver.session() as session:
            rows = session.run(cypher, max_levels=max_levels, min_size=min_size)
            nodes: list[CommunityHierarchyNode] = []
            max_level = 0
            for row in rows:
                r = dict(row)
                lvl = int(r.get("level", 0))
                max_level = max(max_level, lvl)
                nodes.append(
                    CommunityHierarchyNode(
                        community_id=str(r.get("community_id", "")),
                        level=lvl,
                        parent_id=r.get("parent_id"),
                        member_count=int(r.get("member_count", 0)),
                        summary=r.get("summary"),
                        top_terms=r.get("top_terms"),
                    )
                )
            return CommunityHierarchy(levels=max_level + 1 if nodes else 0, nodes=nodes)
    except Exception as exc:
        log_swallowed_error("communities.community_hierarchy", exc)
        raise
