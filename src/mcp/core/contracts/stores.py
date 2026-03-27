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
