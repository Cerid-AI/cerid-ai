# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""ChromaNeo4jRetriever — a `neo4j-graphrag` external-vector retriever
backed by chromadb.

Workstream E Phase 4a.5. The library ships native external-vector
retrievers for Qdrant, Pinecone, and Weaviate; chromadb is not in the
list. This module fills that gap by subclassing
:class:`neo4j_graphrag.retrievers.base.ExternalRetriever` with the
exact same shape (per the Phase 4a.1 spike).

**Layering:** lives in ``core/retrieval/`` and takes the chromadb
collection by injection (duck-typed; matches the
``_CacheBackend`` Protocol pattern from ``semantic_cache.py``). The
``core`` layer therefore has zero direct ``chromadb`` import — the
caller (``app/main.py`` or a router) wires the collection in.

**Flow:**

1. Vector search against the injected chromadb collection.
2. Pull the Neo4j-side identifier from each result (top-level chroma
   id when ``id_property_external == "id"``, otherwise the named key
   inside the result's ``metadatas`` payload — the artifact-level
   pattern Cerid uses).
3. UNWIND the (id, score) pairs into the standardised Cypher tail
   produced by :func:`neo4j_graphrag.retrievers.external.utils.get_match_query`,
   which lets callers add ``return_properties`` / ``retrieval_query``
   for graph expansion (entity neighbourhoods, etc.).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import neo4j
from neo4j_graphrag.embeddings.base import Embedder
from neo4j_graphrag.exceptions import (
    EmbeddingRequiredError,
    SearchValidationError,
)
from neo4j_graphrag.retrievers.base import ExternalRetriever
from neo4j_graphrag.retrievers.external.utils import get_match_query
from neo4j_graphrag.types import (
    RawSearchResult,
    RetrieverResultItem,
    VectorSearchModel,
)
from pydantic import ValidationError

logger = logging.getLogger("ai-companion.graphrag_retriever")


class ChromaNeo4jRetriever(ExternalRetriever):
    """Hybrid retriever: vector search in chromadb + Cypher match in Neo4j.

    The ``id_property_external`` is the metadata field on each chromadb
    result that names the Neo4j node identifier. Default
    ``"artifact_id"`` matches Cerid's artifact-level
    ``(:Artifact)-[:MENTIONS]->(:Entity)`` shape; chunk-level callers
    can pass ``"id"`` to tie the chromadb top-level id directly to a
    Neo4j ``id_property_neo4j`` key.

    Args:
        driver: A live Neo4j driver.
        chroma_collection: A duck-typed chromadb ``Collection`` handle
            (or any object exposing ``.query(query_embeddings, n_results,
            include)`` that returns the standard chromadb dict shape).
        id_property_neo4j: The Neo4j node property that the chromadb
            id maps onto. For our schema, ``"id"`` on ``(:Artifact)``.
        id_property_external: Either ``"id"`` (use chromadb's top-level
            chunk id directly) or a metadata key (e.g. ``"artifact_id"``).
            Default is ``"artifact_id"`` for the artifact-level shape.
        embedder: Optional :class:`Embedder` so callers can pass
            ``query_text`` and have it embedded server-side. If ``None``,
            callers must supply ``query_vector`` directly.
        return_properties: List of node properties returned by the
            standardised Cypher tail.
        retrieval_query: Optional Cypher-tail override for graph
            expansion (e.g. ``"OPTIONAL MATCH (node)-[:MENTIONS]->(e:Entity)
            RETURN node, collect(DISTINCT e) AS entities, score"``).
        node_label_neo4j: Label for the standardised MATCH; default
            ``"Artifact"`` matches our schema.
        result_formatter: Optional formatter for ``RetrieverResultItem``.
        neo4j_database: Optional Neo4j database name (default: server's
            default database).
    """

    VERIFY_NEO4J_VERSION = False  # Cerid uses neo4j 2026.04 calver; lib's 5.x check is too narrow

    def __init__(
        self,
        driver: neo4j.Driver,
        chroma_collection: Any,
        id_property_neo4j: str,
        id_property_external: str = "artifact_id",
        embedder: Optional[Embedder] = None,
        return_properties: Optional[list[str]] = None,
        retrieval_query: Optional[str] = None,
        node_label_neo4j: Optional[str] = "Artifact",
        result_formatter: Optional[
            Callable[[neo4j.Record], RetrieverResultItem]
        ] = None,
        neo4j_database: Optional[str] = None,
    ) -> None:
        super().__init__(
            driver=driver,
            id_property_external=id_property_external,
            id_property_neo4j=id_property_neo4j,
            neo4j_database=neo4j_database,
            node_label_neo4j=node_label_neo4j,
        )
        self.chroma_collection = chroma_collection
        self.embedder = embedder
        self.return_properties = return_properties
        self.retrieval_query = retrieval_query
        self.result_formatter = result_formatter

    # ---------------------------------------------------------------
    # Implementation of ExternalRetriever.get_search_results
    # ---------------------------------------------------------------

    def get_search_results(
        self,
        query_vector: Optional[list[float]] = None,
        query_text: Optional[str] = None,
        top_k: int = 5,
        where: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> RawSearchResult:
        """Run vector search in chromadb, then MATCH in Neo4j.

        ``where`` is a chromadb metadata filter (e.g.
        ``{"domain": {"$eq": "trading"}}``). Forwarded directly to
        ``Collection.query()``. Use it for tenant scoping or
        domain-restricted retrieval.
        """
        try:
            validated = VectorSearchModel(
                query_vector=query_vector,
                query_text=query_text,
                top_k=top_k,
            )
        except ValidationError as exc:
            raise SearchValidationError(exc.errors()) from exc

        if validated.query_text:
            if self.embedder is None:
                raise EmbeddingRequiredError(
                    "No embedder provided for query_text — pass `query_vector` "
                    "or set `embedder=...` on the retriever."
                )
            query_vector = self.embedder.embed_query(validated.query_text)

        chroma_kwargs: dict[str, Any] = {
            "query_embeddings": [query_vector],
            "n_results": top_k,
            "include": ["distances", "metadatas"],
        }
        if where is not None:
            chroma_kwargs["where"] = where

        result = self.chroma_collection.query(**chroma_kwargs)

        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0] if "metadatas" in result else []

        # The "id" column in chromadb is the chunk id; if the caller is
        # tying matches by metadata (artifact-level), pull from there.
        use_top_level_id = self.id_property_external == "id"
        match_params: list[tuple[Any, float]] = []
        for i, chroma_id in enumerate(ids):
            if use_top_level_id:
                match_id: Any = chroma_id
            else:
                meta = metadatas[i] if i < len(metadatas) else {}
                match_id = meta.get(self.id_property_external) if isinstance(meta, dict) else None
                if match_id is None:
                    continue
            distance = distances[i] if i < len(distances) else 0.0
            # Cosine space (Cerid default): score = 1 - distance.
            score = 1.0 - float(distance)
            match_params.append((match_id, score))

        # Dedup by match_id while preserving best score (artifact-level
        # mode collapses many chunks per artifact).
        if not use_top_level_id and match_params:
            best: dict[Any, float] = {}
            for mid, sc in match_params:
                if mid not in best or sc > best[mid]:
                    best[mid] = sc
            match_params = sorted(best.items(), key=lambda t: t[1], reverse=True)[:top_k]

        match_query = get_match_query(
            return_properties=self.return_properties,
            retrieval_query=self.retrieval_query,
            node_label=self.node_label_neo4j,
        )
        parameters = {
            "match_params": match_params,
            "id_property": self.id_property_neo4j,
        }
        logger.debug(
            "ChromaNeo4jRetriever Cypher: query=%r params_count=%d",
            match_query, len(match_params),
        )

        records, _, _ = self.driver.execute_query(
            match_query, parameters_=parameters, database_=self.neo4j_database,
        )
        return RawSearchResult(records=records)


# ---------------------------------------------------------------------------
# Helper: entity-neighborhood expansion for query_agent step-6 swap
# ---------------------------------------------------------------------------

def entity_neighborhood_artifact_ids(
    driver: neo4j.Driver,
    seed_artifact_ids: list[str],
    *,
    top_k: int = 10,
    min_shared_entities: int = 1,
    exclude_seeds: bool = True,
    neo4j_database: Optional[str] = None,
) -> list[tuple[str, int]]:
    """Find artifacts that share at least ``min_shared_entities`` entities
    with any of the seed artifacts, ranked by share count.

    Used by ``core/agents/query_agent.py`` step-6 when
    ``RETRIEVAL_MODE=local_graphrag``: instead of expanding via
    Domain/SubCategory/Tag relationships, expand via shared
    ``(:MENTIONS)`` neighbours produced by entity extraction.

    Returns ``[(artifact_id, shared_entity_count), ...]`` sorted by
    count desc, capped at ``top_k``. Seeds themselves are filtered
    out by default; callers can flip ``exclude_seeds`` to keep them
    (useful for "all artifacts in the same entity cluster" queries).
    """
    if not seed_artifact_ids:
        return []

    cypher = """
    MATCH (seed:Artifact) WHERE seed.id IN $seed_ids
    MATCH (seed)-[:MENTIONS]->(e:Entity)<-[:MENTIONS]-(other:Artifact)
    WHERE ($exclude_seeds = false OR NOT other.id IN $seed_ids)
    WITH other, count(DISTINCT e) AS shared
    WHERE shared >= $min_shared
    RETURN other.id AS artifact_id, shared
    ORDER BY shared DESC, other.id ASC
    LIMIT $top_k
    """
    records, _, _ = driver.execute_query(
        cypher,
        parameters_={
            "seed_ids": list(seed_artifact_ids),
            "exclude_seeds": exclude_seeds,
            "min_shared": min_shared_entities,
            "top_k": top_k,
        },
        database_=neo4j_database,
    )
    return [(r["artifact_id"], int(r["shared"])) for r in records]
