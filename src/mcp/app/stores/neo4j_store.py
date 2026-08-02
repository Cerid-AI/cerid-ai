# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j implementation of GraphStore contract.

Every method here is ``async`` per the GraphStore contract, but the
underlying neo4j Python driver calls are synchronous blocking I/O. Calling
``driver.execute_query()`` or ``session.run()`` directly from the event
loop pins the loop for the duration of the Cypher roundtrip — on graph
expansion with 2-hop traversal across a moderate artifact graph this can
exceed the 45s event-loop watchdog and kill the process.

Every call site therefore wraps the sync driver in ``asyncio.to_thread``
so the event loop stays responsive AND in ``with_timeout`` so a slow
graph traversal can't hang the request past the per-call budget.
"""

from __future__ import annotations

import asyncio
from typing import Any

from config.constants import NEO4J_QUERY_TIMEOUT
from core.contracts.stores import ArtifactNode, GraphStore
from core.utils.timeouts import with_timeout


def _to_artifact_node(raw: dict[str, Any]) -> ArtifactNode | None:
    """Map a db/neo4j row dict onto an ``ArtifactNode``.

    The db layer keys the artifact id ``"id"`` while this adapter was
    written expecting ``"artifact_id"`` — the mismatch made every graph
    lookup on the query path raise ``KeyError: 'artifact_id'`` (swallowed
    twice per request under ``core.agents.query_agent``; 2026-07-12 beta
    triage). Accept both keys and skip rows carrying neither.

    ``quality_score`` is absent from the db rows; default to 0.5 — the
    same neutral value the query agent assumes for unknown artifacts —
    so a missing score never reads as "worst quality" (0.0).
    """
    node_id = raw.get("artifact_id") or raw.get("id")
    if not node_id:
        return None
    return ArtifactNode(
        id=node_id, filename=raw.get("filename", ""),
        domain=raw.get("domain", ""), sub_category=raw.get("sub_category", ""),
        tags=raw.get("tags", []), summary=raw.get("summary", ""),
        quality_score=raw.get("quality_score", 0.5),
    )


class Neo4jGraphStore(GraphStore):
    """GraphStore backed by Neo4j — wraps db/neo4j/ CRUD operations.

    All Neo4j driver calls are offloaded to a worker thread via
    ``asyncio.to_thread`` so graph expansion can't block the event loop
    long enough to trip the watchdog. Each is then wrapped in
    ``with_timeout`` so a slow query surfaces a typed error to the
    caller instead of hanging until the request budget runs out.
    """

    def __init__(self, driver: Any) -> None:
        self._driver = driver

    async def get_artifact(self, artifact_id: str) -> ArtifactNode | None:
        from app.db.neo4j.artifacts import get_artifact
        raw = await with_timeout(
            asyncio.to_thread(get_artifact, self._driver, artifact_id),
            seconds=NEO4J_QUERY_TIMEOUT,
            label="neo4j.get_artifact",
            context={"artifact_id": artifact_id},
        )
        if not raw:
            return None
        return _to_artifact_node(raw)

    async def get_quality_and_summaries(
        self, artifact_ids: list[str]
    ) -> tuple[dict[str, float], dict[str, str]]:
        """Real single-round-trip override — UNWINDs the whole candidate set
        in one Cypher query instead of the ABC default's per-artifact fan-out.
        """
        from app.db.neo4j.artifacts import get_quality_and_summaries
        return await with_timeout(
            asyncio.to_thread(get_quality_and_summaries, self._driver, artifact_ids),
            seconds=NEO4J_QUERY_TIMEOUT,
            label="neo4j.get_quality_and_summaries",
            context={"artifact_count": len(artifact_ids)},
        )

    async def get_related(
        self, artifact_ids: list[str], *, depth: int = 1, limit: int = 20,
    ) -> list[ArtifactNode]:
        from app.db.neo4j import find_related_artifacts
        raw_list = await with_timeout(
            asyncio.to_thread(
                find_related_artifacts, self._driver, artifact_ids,
                depth=depth, max_results=limit,
            ),
            seconds=NEO4J_QUERY_TIMEOUT,
            label="neo4j.get_related",
            context={"seed_count": len(artifact_ids), "depth": depth, "limit": limit},
        )
        return [
            node for r in raw_list if (node := _to_artifact_node(r)) is not None
        ]

    async def list_artifacts(
        self, *, domain: str | None = None, offset: int = 0, limit: int = 100,
    ) -> list[ArtifactNode]:
        from app.db.neo4j.artifacts import list_artifacts
        raw_list = await with_timeout(
            asyncio.to_thread(
                list_artifacts, self._driver,
                domain=domain, offset=offset, limit=limit,
            ),
            seconds=NEO4J_QUERY_TIMEOUT,
            label="neo4j.list_artifacts",
            context={"domain": domain or "<all>", "limit": limit},
        )
        return [
            node for r in raw_list if (node := _to_artifact_node(r)) is not None
        ]

    async def update_artifact(self, artifact_id: str, updates: dict[str, Any]) -> None:
        from app.db.neo4j.artifacts import update_artifact_summary
        summary = updates.get("summary", "")
        await with_timeout(
            asyncio.to_thread(
                update_artifact_summary, self._driver, artifact_id, summary,
            ),
            seconds=NEO4J_QUERY_TIMEOUT,
            label="neo4j.update_artifact",
            context={"artifact_id": artifact_id},
        )

    async def list_domains(self) -> list[str]:
        def _query_domains() -> list[str]:
            records, _, _ = self._driver.execute_query(
                "MATCH (a:Artifact) RETURN DISTINCT a.domain AS domain ORDER BY domain"
            )
            return [r["domain"] for r in records if r["domain"]]

        return await with_timeout(
            asyncio.to_thread(_query_domains),
            seconds=NEO4J_QUERY_TIMEOUT,
            label="neo4j.list_domains",
        )
