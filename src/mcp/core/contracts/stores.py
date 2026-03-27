# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Abstract store contracts — VectorStore and GraphStore."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class SearchResult:
    """A single vector search result."""

    artifact_id: str
    chunk_id: str
    content: str
    metadata: dict[str, Any]
    distance: float


@dataclass
class ArtifactNode:
    """Core artifact metadata from the knowledge graph."""

    id: str
    filename: str
    domain: str
    sub_category: str
    tags: list[str]
    summary: str
    quality_score: float


class VectorStore(ABC):
    """Abstract vector store — ChromaDB, Pinecone, Weaviate, etc."""

    @abstractmethod
    async def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 10,
        where: dict[str, Any] | None = None,
        where_document: dict[str, Any] | None = None,
    ) -> list[SearchResult]: ...

    @abstractmethod
    async def get_by_ids(self, ids: list[str]) -> list[SearchResult]: ...

    @abstractmethod
    async def count(self) -> int: ...


class GraphStore(ABC):
    """Abstract knowledge graph — Neo4j, ArangoDB, etc."""

    @abstractmethod
    async def get_artifact(self, artifact_id: str) -> ArtifactNode | None: ...

    @abstractmethod
    async def get_related(
        self,
        artifact_ids: list[str],
        *,
        depth: int = 1,
        limit: int = 20,
    ) -> list[ArtifactNode]: ...

    @abstractmethod
    async def list_artifacts(
        self,
        *,
        domain: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[ArtifactNode]: ...

    @abstractmethod
    async def update_artifact(
        self, artifact_id: str, updates: dict[str, Any]
    ) -> None: ...

    @abstractmethod
    async def list_domains(self) -> list[str]: ...

    # -- Batch helpers used by the query agent pipeline --

    async def get_artifacts_batch(
        self, artifact_ids: list[str]
    ) -> dict[str, ArtifactNode]:
        """Batch-fetch multiple artifacts by ID.

        Returns ``{artifact_id: ArtifactNode}`` for all IDs that exist.
        Default implementation calls :meth:`get_artifact` in parallel;
        concrete stores should override with a single round-trip query.
        """
        import asyncio

        results: dict[str, ArtifactNode] = {}
        nodes = await asyncio.gather(
            *(self.get_artifact(aid) for aid in artifact_ids)
        )
        for node in nodes:
            if node is not None:
                results[node.id] = node
        return results

    async def find_related_with_metadata(
        self,
        artifact_ids: list[str],
        *,
        depth: int = 1,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Find related artifacts with full relationship metadata.

        Returns a list of dicts with keys: ``id``, ``filename``, ``domain``,
        ``summary``, ``keywords``, ``chunk_ids``, ``chunk_count``,
        ``relationship_type``, ``relationship_depth``, ``relationship_reason``.

        Default implementation delegates to :meth:`get_related` and wraps
        the :class:`ArtifactNode` results (without relationship metadata).
        Concrete stores should override to include relationship details.
        """
        nodes = await self.get_related(
            artifact_ids, depth=depth, limit=limit,
        )
        return [
            {
                "id": n.id,
                "filename": n.filename,
                "domain": n.domain,
                "summary": n.summary,
                "keywords": "[]",
                "chunk_ids": "[]",
                "chunk_count": 0,
                "relationship_type": "",
                "relationship_depth": 1,
                "relationship_reason": "",
            }
            for n in nodes
        ]
