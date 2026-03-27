# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Neo4j implementation of GraphStore contract."""

from __future__ import annotations

from typing import Any

from core.contracts.stores import ArtifactNode, GraphStore


class Neo4jGraphStore(GraphStore):
    """GraphStore backed by Neo4j -- wraps db/neo4j/ CRUD operations."""

    def __init__(self, driver: Any) -> None:
        self._driver = driver

    async def get_artifact(self, artifact_id: str) -> ArtifactNode | None:
        from db.neo4j.artifacts import get_artifact
        raw = await get_artifact(self._driver, artifact_id)
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
        from db.neo4j import find_related_artifacts
        raw_list = await find_related_artifacts(self._driver, artifact_ids, depth=depth, limit=limit)
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
        from db.neo4j.artifacts import list_artifacts
        raw_list = await list_artifacts(self._driver, domain=domain, offset=offset, limit=limit)
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
        from db.neo4j.artifacts import update_artifact_summary
        await update_artifact_summary(self._driver, artifact_id, updates)

    async def list_domains(self) -> list[str]:
        from db.neo4j.artifacts import list_domains
        return await list_domains(self._driver)
