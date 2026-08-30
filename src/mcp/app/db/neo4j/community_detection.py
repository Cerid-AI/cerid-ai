# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Leiden community detection over the entity graph.

Workstream E Phase 4b.1. Projects the (:Entity) co-mention graph into
the Neo4j GDS catalog, runs Leiden, materialises ``(:Community)``
nodes and ``(:Entity)-[:IN_COMMUNITY]->(:Community)`` edges. Idempotent
— each invocation overwrites the previous community partition for the
named projection.

**Co-mention semantics:** two entities are connected by an undirected
edge if at least one ``(:Artifact)`` MENTIONS both. The edge weight is
the count of shared artifacts (proxy for "how often these two entities
appear together"). Leiden then partitions the graph maximising
weighted modularity.

Hierarchical levels: Leiden produces a tree of communities (level 0 =
fine-grained, higher levels merge). All levels are materialised so
:func:`core.agents.query_router` (Phase 4b.3) can pick the right
granularity per query.

Layering: lives in ``app/db/neo4j/`` because it directly consumes
the ``graphdatascience`` Python client + neo4j driver. Pure data-
access; no FastAPI, no LLM.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.community_detection")


def _projection_name() -> str:
    """Generate a unique GDS projection name per run.

    GDS projections are global state on the Neo4j server. Using a
    UUID-suffixed name avoids stomping on a concurrent run (e.g.
    scheduler kicked off while a manual trigger is running).
    """
    return f"cerid-entities-{uuid.uuid4().hex[:8]}"


def detect_communities(
    driver: Any,
    *,
    min_community_size: int = 2,
    max_levels: int = 5,
    neo4j_database: str | None = None,
) -> dict[str, Any]:
    """Run Leiden over the entity graph and write Community nodes.

    Pipeline:

    1. Project the (:Entity) co-mention graph into a GDS catalog.
    2. Run ``gds.leiden.write`` to assign ``communityId`` per Entity
       across all hierarchical levels.
    3. Drop existing ``[:IN_COMMUNITY]`` edges (idempotent re-run).
    4. Materialise (or update) (:Community) nodes per (level, id).
    5. Create [:IN_COMMUNITY] edges from each Entity to its community.
    6. Drop the GDS projection.

    Returns: ``{"projection": str, "elapsed_seconds": float,
    "communities_per_level": {0: 12, 1: 8, ...}, "edges": int,
    "modularity": float}``.

    Skips entirely if there are zero entities — the caller logs a
    warning and the scheduler can re-arm next cycle.
    """
    from graphdatascience import GraphDataScience

    started = time.time()

    with driver.session(database=neo4j_database) as session:
        ent_count = session.run("MATCH (e:Entity) RETURN count(e) AS n").single()["n"]
        if ent_count == 0:
            logger.warning("No entities in graph — skipping community detection")
            return {"skipped": "no_entities"}

    gds = GraphDataScience.from_neo4j_driver(driver, database=neo4j_database)
    proj_name = _projection_name()
    stats: dict[str, Any] = {"projection": proj_name}

    # Materialise CO_MENTIONED edges in Neo4j first. Leiden requires
    # UNDIRECTED relationships in the projection, and the GDS native
    # projection takes orientation as configuration. We persist these
    # edges as first-class graph data so callers can query them
    # outside the GDS catalog too — they're the "two entities appear
    # together" view of the corpus.
    with driver.session(database=neo4j_database) as session:
        session.run("MATCH (:Entity)-[r:CO_MENTIONED]-(:Entity) DELETE r")
        session.run(
            """
            MATCH (e1:Entity)<-[:MENTIONS]-(a:Artifact)-[:MENTIONS]->(e2:Entity)
            WHERE id(e1) < id(e2)
            WITH e1, e2, count(DISTINCT a) AS w
            MERGE (e1)-[r:CO_MENTIONED]->(e2)
            SET r.weight = w
            """
        )

    try:
        # Native projection with explicit UNDIRECTED orientation.
        graph, _ = gds.graph.project(
            proj_name,
            "Entity",
            {
                "CO_MENTIONED": {
                    "orientation": "UNDIRECTED",
                    "properties": ["weight"],
                }
            },
        )
        stats["nodes_projected"] = int(graph.node_count())
        stats["edges_projected"] = int(graph.relationship_count())
        if stats["edges_projected"] == 0:
            logger.warning(
                "Entity graph has no co-mention edges — Leiden requires "
                "≥1 edge; skipping."
            )
            graph.drop()
            stats["skipped"] = "no_co_mention_edges"
            return stats

        # AF-034: reset last run's partition markers before Leiden re-writes
        # them, so a stale id can't survive. gds.leiden.write only writes
        # leiden_communityIds for entities in THIS run's projection (those with
        # ≥1 CO_MENTIONED edge); an entity that dropped out of the projection
        # keeps last run's leiden_communityIds AND community_id. Left in place,
        # the write-back loop below would re-derive a stale community_id from
        # those stale ids and resurrect the entity's old Community via MERGE.
        # Clearing both here means only this run's projected entities carry a
        # partition; every dropped/orphan entity falls through to the
        # 'isolated' sentinel sweep at the end.
        with driver.session(database=neo4j_database) as session:
            session.run("MATCH (e:Entity) REMOVE e.leiden_communityIds, e.community_id")

        # Run Leiden, write back communityId across all levels.
        result = gds.leiden.write(
            graph,
            writeProperty="leiden_communityIds",  # list[long] — one per level
            relationshipWeightProperty="weight",
            includeIntermediateCommunities=True,
            maxLevels=max_levels,
        )
        stats["modularity"] = float(result["modularity"])
        stats["levels_produced"] = int(result.get("ranLevels", max_levels))
        graph.drop()
    finally:
        # Best-effort cleanup if anything above raised after projection.
        try:
            if gds.graph.exists(proj_name)["exists"]:
                gds.graph.drop(proj_name)
        except Exception as exc:
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error(
                "app.db.neo4j.community_detection.cleanup", exc,
            )

    # Read communities back from leiden_communityIds and write Community
    # nodes + IN_COMMUNITY edges. Done in Cypher so we get atomic-per-
    # level transactions.
    now = utcnow_iso()
    communities_seen: dict[int, set[str]] = {}
    edges_total = 0
    with driver.session(database=neo4j_database) as session:
        # Drop existing IN_COMMUNITY edges so the partition can be rebuilt.
        # We deliberately do NOT delete Community nodes here: the MERGE below
        # re-attaches each recurring community by id and (via ON MATCH)
        # preserves its cached .summary, so the LLM summary cost-guard
        # (community_summaries: WHERE c.summary IS NULL) still skips them.
        # Stale communities from the prior partition are pruned AFTER the
        # rebuild, once we know which ids survived.
        session.run("MATCH (:Entity)-[r:IN_COMMUNITY]->(:Community) DELETE r")

        # Walk every Entity, write its [level,id] community pairs.
        rows = list(session.run(
            "MATCH (e:Entity) WHERE e.leiden_communityIds IS NOT NULL "
            "RETURN e.canonical_id AS canonical_id, e.leiden_communityIds AS ids"
        ))
        for row in rows:
            for level, comm_native_id in enumerate(row["ids"]):
                community_id = f"{level}:{comm_native_id}"
                summary = session.run(
                    """
                    MERGE (c:Community {id: $cid})
                      ON CREATE SET
                        c.level = $level,
                        c.native_id = $native,
                        c.created_at = $now,
                        c.updated_at = $now
                      ON MATCH SET c.updated_at = $now
                    WITH c
                    MATCH (e:Entity {canonical_id: $entity_id})
                    MERGE (e)-[:IN_COMMUNITY]->(c)
                    // Level 0 = finest partition. Expose it as the scalar
                    // e.community_id the graph renderers (app/routers/graph.py
                    // neighborhood + embeddings/3d, processor/jobs/compute_umap_3d)
                    // actually read — they never parsed leiden_communityIds, so
                    // node coloring was null for every entity. Higher levels stay
                    // in leiden_communityIds for hierarchical drill-up.
                    SET e.community_id = CASE
                        WHEN $level = 0 THEN $cid ELSE e.community_id
                    END
                    """,
                    cid=community_id,
                    level=level,
                    native=int(comm_native_id),
                    now=now,
                    entity_id=row["canonical_id"],
                ).consume()
                edges_total += summary.counters.relationships_created
                communities_seen.setdefault(level, set()).add(community_id)

        # Prune communities that existed in a PRIOR partition but are absent
        # from this run. Recurring ids were preserved above (with their cached
        # summaries); only genuinely-stale communities are removed. Replaces
        # the old blanket pre-delete that discarded every summary each run.
        seen_ids = [cid for ids in communities_seen.values() for cid in ids]
        session.run(
            "MATCH (c:Community) WHERE NOT c.id IN $seen DETACH DELETE c",
            seen=seen_ids,
        )

        # Drop tiny communities below the size threshold (Leiden often
        # produces singletons — they're noise and add scheduler load).
        if min_community_size > 1:
            session.run(
                """
                MATCH (c:Community)<-[r:IN_COMMUNITY]-(e:Entity)
                WITH c, count(DISTINCT e) AS size
                WHERE size < $threshold
                MATCH (c)<-[r:IN_COMMUNITY]-()
                DELETE r
                WITH c
                MATCH (c) WHERE NOT (c)<-[:IN_COMMUNITY]-() DELETE c
                """,
                threshold=min_community_size,
            )

        # Assign degree-0 orphan entities the 'isolated' sentinel.
        #
        # This used to test `community_id IS NULL` alone, on the stated theory
        # that "GDS projections include only nodes that participate in at least
        # one CO_MENTIONED edge, so Leiden never touches pure orphans". That is
        # not what a native label projection does. gds.graph.project(name,
        # "Entity", {...}) above projects EVERY Entity node, edges or not, so
        # Leiden hands each orphan its own singleton community and the
        # write-back loop stamps a real '{level}:{native}' id on it. The NULL
        # test then matched nothing and the sentinel was never assigned.
        #
        # In production (min_community_size=2) the prune above then deletes that
        # singleton Community and its IN_COMMUNITY edge but leaves the id on the
        # entity, so orphans carried a community_id pointing at a Community that
        # no longer existed — coloured by the renderers as members of a live
        # community, and counted by graph.py's community_count.
        #
        # Orphan-ness is asked of the graph directly: no CO_MENTIONED edge.
        # Those edges are rebuilt from MENTIONS at the top of this function, so
        # within a run they are exactly this partition's adjacency. Entities in
        # a pruned but genuinely multi-member community are deliberately NOT
        # swept — that is a separate policy question and the serving stack
        # already treats them as peripheral either way.
        session.run(
            """
            MATCH (e:Entity) WHERE NOT (e)-[:CO_MENTIONED]-()
            OPTIONAL MATCH (e)-[r:IN_COMMUNITY]->(:Community)
            DELETE r
            """
        )
        isolated_summary = session.run(
            """
            MATCH (e:Entity)
            WHERE e.community_id IS NULL OR NOT (e)-[:CO_MENTIONED]-()
            SET e.community_id = 'isolated'
            """
        ).consume()
        stats["isolated_assigned"] = isolated_summary.counters.properties_set

        # No Community cleanup here on purpose. A labelled
        # `MATCH (c:Community) ... DELETE c` is exactly the blanket wipe
        # test_detection_preserves_community_summaries forbids: it discards the
        # cached LLM `.summary` on every recurring community and defeats the
        # summary cost-guard. It is also unnecessary — in production
        # (min_community_size=2) the tiny-community prune above has already
        # removed singleton communities, and any that survive one run are
        # reaped by the stale-id prune on the next.

    stats["communities_per_level"] = {
        level: len(comms) for level, comms in communities_seen.items()
    }
    stats["edges"] = edges_total
    stats["elapsed_seconds"] = round(time.time() - started, 2)
    logger.info("Leiden complete: %s", stats)

    # AF-035: re-clustering leaves the cerid:graph:emb3d:* serving cache stale.
    # This module is a pure Neo4j DAL (no redis import by design), so the bust
    # lives in the caller — scheduler.py::_run_community_refresh busts
    # cerid:graph:emb3d:* (via _bust_job_caches) after detect_communities returns.
    return stats


def list_communities(
    driver: Any,
    *,
    level: int | None = None,
    neo4j_database: str | None = None,
) -> list[dict[str, Any]]:
    """Read-back helper for tests + introspection.

    When ``level`` is provided, returns only communities at that
    hierarchical depth; otherwise returns all levels.
    """
    cypher = "MATCH (c:Community)<-[:IN_COMMUNITY]-(e:Entity)"
    params: dict = {}
    if level is not None:
        cypher += " WHERE c.level = $level"
        params["level"] = level
    cypher += (
        " WITH c, collect(DISTINCT e.canonical_id) AS members"
        " RETURN c.id AS id, c.level AS level, c.native_id AS native_id, "
        "        size(members) AS size, members"
        " ORDER BY c.level ASC, size DESC"
    )
    with driver.session(database=neo4j_database) as session:
        return [dict(r) for r in session.run(cypher, **params)]
