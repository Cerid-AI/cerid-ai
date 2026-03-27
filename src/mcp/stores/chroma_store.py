# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""ChromaDB implementation of VectorStore contract."""

from __future__ import annotations

from typing import Any

from core.contracts.stores import SearchResult, VectorStore


class ChromaVectorStore(VectorStore):
    """VectorStore backed by a ChromaDB collection."""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 10,
        where: dict[str, Any] | None = None,
        where_document: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        kwargs: dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": top_k,
        }
        if where:
            kwargs["where"] = where
        if where_document:
            kwargs["where_document"] = where_document

        result = self._collection.query(**kwargs)

        results: list[SearchResult] = []
        if result and result.get("ids") and result["ids"][0]:
            ids = result["ids"][0]
            docs = result["documents"][0] if result.get("documents") else [""] * len(ids)
            metas = result["metadatas"][0] if result.get("metadatas") else [{}] * len(ids)
            dists = result["distances"][0] if result.get("distances") else [0.0] * len(ids)
            for i, chunk_id in enumerate(ids):
                meta = metas[i] or {}
                results.append(
                    SearchResult(
                        artifact_id=meta.get("artifact_id", ""),
                        chunk_id=chunk_id,
                        content=docs[i],
                        metadata=meta,
                        distance=dists[i],
                    )
                )
        return results

    async def get_by_ids(self, ids: list[str]) -> list[SearchResult]:
        result = self._collection.get(ids=ids, include=["documents", "metadatas"])
        results: list[SearchResult] = []
        if result and result.get("ids"):
            for i, chunk_id in enumerate(result["ids"]):
                meta = (result.get("metadatas") or [{}])[i] or {}
                doc = (result.get("documents") or [""])[i]
                results.append(
                    SearchResult(
                        artifact_id=meta.get("artifact_id", ""),
                        chunk_id=chunk_id,
                        content=doc,
                        metadata=meta,
                        distance=0.0,
                    )
                )
        return results

    async def count(self) -> int:
        return self._collection.count()
