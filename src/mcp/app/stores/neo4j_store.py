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
so the event loop stays responsive and the heartbeat keeps ticking even
under heavy graph traversal load.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.contracts.stores import ArtifactNode, GraphStore


class Neo4jGraphStore(GraphStore):
    """GraphStore backed by Neo4j — wraps db/neo4j/ CRUD operations.

    All Neo4j driver calls are offloaded to a worker thread via
    ``asyncio.to_thread`` so graph expansion can't block the event loop
    long enough to trip the watchdog.
    """

    def __init__(self, driver: Any) -> None:
        self._driver = driver

    async def get_artifact(self, artifact_id: str) -> ArtifactNode | None:
        from app.db.neo4j.artifacts import get_artifact
        raw = await asyncio.to_thread(get_artifact, self._driver, artifact_id)
        if not raw:
            return None
        return ArtifactNode(
            id=raw["artifact_id"], filename=raw.get("filename", ""),
            domain=raw.get("domain", ""), sub_category=raw.get("sub_category", ""),
            tags=raw.get("tags", []), summary=raw.get("summary", ""),
            quality_score=raw.get("quality_score", 0.0),
        )

    async def get_related(
        self, artifact_ids: list[str], *, depth: int = 1, limit: int = 20,
    ) -> list[ArtifactNode]:
        from app.db.neo4j import find_related_artifacts
        raw_list = await asyncio.to_thread(
            find_related_artifacts, self._driver, artifact_ids,
            depth=depth, max_results=limit,
        )
        return [
            ArtifactNode(
                id=r["artifact_id"], filename=r.get("filename", ""),
                domain=r.get("domain", ""), sub_category=r.get("sub_category", ""),
                tags=r.get("tags", []), summary=r.get("summary", ""),
                quality_score=r.get("quality_score", 0.0),
            )
            for r in raw_list
        ]

    async def list_artifacts(
        self, *, domain: str | None = None, offset: int = 0, limit: int = 100,
    ) -> list[ArtifactNode]:
        from app.db.neo4j.artifacts import list_artifacts
        raw_list = await asyncio.to_thread(
            list_artifacts, self._driver,
            domain=domain, offset=offset, limit=limit,
        )
        return [
            ArtifactNode(
                id=r["artifact_id"], filename=r.get("filename", ""),
                domain=r.get("domain", ""), sub_category=r.get("sub_category", ""),
                tags=r.get("tags", []), summary=r.get("summary", ""),
                quality_score=r.get("quality_score", 0.0),
            )
            for r in raw_list
        ]

    async def update_artifact(self, artifact_id: str, updates: dict[str, Any]) -> None:
        from app.db.neo4j.artifacts import update_artifact_summary
        summary = updates.get("summary", "")
        await asyncio.to_thread(
            update_artifact_summary, self._driver, artifact_id, summary,
        )

    async def list_domains(self) -> list[str]:
        def _query_domains() -> list[str]:
            records, _, _ = self._driver.execute_query(
                "MATCH (a:Artifact) RETURN DISTINCT a.domain AS domain ORDER BY domain"
            )
            return [r["domain"] for r in records if r["domain"]]

        return await asyncio.to_thread(_query_domains)
