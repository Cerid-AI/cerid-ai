# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""ChromaDB implementation of VectorStore contract.

The chromadb Python client is sync-blocking. Every call site offloads
to a worker thread via ``asyncio.to_thread`` so the event loop stays
responsive, then wraps the awaitable in ``with_timeout`` so a slow
collection can't hang the whole RAG path past the per-call budget.
"""

from __future__ import annotations

import asyncio
from typing import Any

from config.constants import CHROMA_QUERY_TIMEOUT
from core.contracts.stores import SearchResult, VectorStore
from core.utils.timeouts import with_timeout


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

        result = await with_timeout(
            asyncio.to_thread(self._collection.query, **kwargs),
            seconds=CHROMA_QUERY_TIMEOUT,
            label="chroma.query",
            context={"top_k": top_k},
        )

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
        result = await with_timeout(
            asyncio.to_thread(
                self._collection.get, ids=ids, include=["documents", "metadatas"],
            ),
            seconds=CHROMA_QUERY_TIMEOUT,
            label="chroma.get_by_ids",
            context={"id_count": len(ids)},
        )
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
        return await with_timeout(
            asyncio.to_thread(self._collection.count),
            seconds=CHROMA_QUERY_TIMEOUT,
            label="chroma.count",
        )
