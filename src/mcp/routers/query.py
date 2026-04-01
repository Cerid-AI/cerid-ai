# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Query endpoint and query_knowledge service function."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

import config
from deps import get_chroma
from errors import RetrievalError
from utils.time import utcnow_iso

router = APIRouter()
logger = logging.getLogger("ai-companion")


def query_knowledge(query: str, domain: str = "general", top_k: int = 3) -> dict:
    """Public — also called by mcp_sse.py execute_tool."""
    chroma = get_chroma()
    coll_name = config.collection_name(domain)
    collection = chroma.get_or_create_collection(name=coll_name)
    results = collection.query(
        query_texts=[query],
        n_results=top_k,
        include=["documents", "distances", "metadatas"],
    )
    timestamp = utcnow_iso()
    if not results.get("documents") or not results["documents"][0]:
        return {"context": "", "sources": [], "confidence": 0.0, "timestamp": timestamp}

    docs = results["documents"][0]
    distances = results.get("distances", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    chunk_ids = results.get("ids", [[]])[0]

    # BM25 hybrid scoring
    bm25_scores: dict = {}
    try:
        from utils import bm25 as bm25_mod
        if bm25_mod.is_available():
            bm25_hits = bm25_mod.search_bm25(domain, query, top_k=top_k)
            if bm25_hits:
                bm25_scores = dict(bm25_hits)
    except (RetrievalError, ValueError, OSError, RuntimeError) as e:
        logger.debug(f"BM25 hybrid scoring unavailable: {e}")

    sources = []
    for i, doc in enumerate(docs):
        dist = distances[i] if i < len(distances) else 1.0
        vector_score = max(0.0, min(1.0, 1.0 - dist))
        meta = metadatas[i] if i < len(metadatas) else {}
        cid = chunk_ids[i] if i < len(chunk_ids) else ""

        if bm25_scores:
            kw_score = bm25_scores.get(cid, 0.0)
            relevance = (
                config.HYBRID_VECTOR_WEIGHT * vector_score
                + config.HYBRID_KEYWORD_WEIGHT * kw_score
            )
        else:
            relevance = vector_score

        sources.append({
            "content": doc[:200],
            "relevance": round(relevance, 3),
            "artifact_id": meta.get("artifact_id", ""),
            "filename": meta.get("filename", ""),
            "domain": meta.get("domain", domain),
            "chunk_index": meta.get("chunk_index", 0),
        })

    TOKEN_BUDGET_CHARS = config.QUERY_CONTEXT_MAX_CHARS
    context_parts = []
    char_count = 0
    for doc in docs:
        if char_count + len(doc) > TOKEN_BUDGET_CHARS:
            remaining = TOKEN_BUDGET_CHARS - char_count
            if remaining > 200:
                context_parts.append(doc[:remaining] + "\n[...truncated for token budget...]")
            break
        context_parts.append(doc)
        char_count += len(doc)

    context = "\n\n".join(context_parts)
    avg_relevance = sum(s["relevance"] for s in sources) / len(sources) if sources else 0.0

    return {
        "context": context,
        "sources": sources,
        "confidence": round(avg_relevance, 3),
        "timestamp": timestamp,
    }


class QueryRequest(BaseModel):
    query: str
    domain: str = "general"
    top_k: int = Field(3, ge=1, le=100)


@router.post("/query")
async def query_endpoint(req: QueryRequest, request: Request):
    import asyncio

    from utils.query_cache import get_cached, set_cached

    # Private mode: level >= 2 skips KB context injection (return empty context)
    client_id = request.headers.get("X-Client-ID", "unknown")
    try:
        from utils.private_mode import get_private_mode_level
        private_level = get_private_mode_level(client_id)
    except (RetrievalError, ValueError, OSError, RuntimeError) as e:
        logger.warning("Private mode check failed (defaulting to disabled): %s", e)
        private_level = 0

    if private_level >= 2:
        logger.info("Private mode level %d: skipping KB context for client %s", private_level, client_id)
        return {"context": "", "sources": [], "confidence": 0.0, "timestamp": utcnow_iso()}

    cached = get_cached(req.query, req.domain, req.top_k)
    if cached:
        return cached
    result = await asyncio.to_thread(query_knowledge, req.query, req.domain, req.top_k)
    set_cached(req.query, req.domain, req.top_k, result)
    return result
