# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""KB search endpoint — routes through the canonical retrieval path.

Phase 1 (2026-06-29) collapsed the legacy ``query_knowledge`` implementation
(single-collection, BM25-hybrid, no rerank / no provenance / no exclude_packs)
onto ``core.agents.query_agent.agent_query_full`` so ``/query`` gets the same
pipeline as every other surface. The ``{context, sources, confidence,
timestamp}`` response shape is preserved for existing SDK/UI consumers.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.deps import get_chroma, get_graph_store, get_neo4j, get_redis
from app.services.private_mode import private_blocks
from core.utils.time import utcnow_iso


# --- Response models (generated: single-return dict-literal routes) ---
class QueryEndpointResponse(BaseModel):
    context: Any
    sources: Any
    confidence: Any
    timestamp: Any



router = APIRouter()
logger = logging.getLogger("ai-companion")


class QueryRequest(BaseModel):
    query: str
    domain: str = "general"
    top_k: int = Field(3, ge=1, le=100)
    exclude_packs: bool = Field(
        False,
        description="Drop knowledge-pack chunks from retrieval (personal-first KB search).",
    )


@router.post("/query", response_model=QueryEndpointResponse)
async def query_endpoint(req: QueryRequest):
    """KB search over the canonical retrieval path (rerank, provenance,
    ``exclude_packs``, tenant-scope). ``external_augmentation`` is off — this is
    a KB search, not an agentic query, so it never fires external sources.
    """
    # Private Mode L2 ("skip KB") — server-side enforcement. No response
    # field survives to signal the bypass (QueryEndpointResponse has no
    # extra="allow"), so the empty results ARE the signal.
    if private_blocks(2):
        return {
            "context": "",
            "sources": [],
            "confidence": 0.0,
            "timestamp": utcnow_iso(),
        }

    from core.agents.query_agent import agent_query_full

    result = await agent_query_full(
        query=req.query,
        domains=[req.domain],
        top_k=req.top_k,
        exclude_packs=req.exclude_packs,
        external_augmentation=False,
        chroma_client=get_chroma(),
        redis_client=get_redis(),
        neo4j_driver=get_neo4j(),
        graph_store=get_graph_store(),
    )
    return {
        "context": result.get("context", ""),
        "sources": result.get("sources", []),
        "confidence": result.get("confidence", 0.0),
        "timestamp": utcnow_iso(),
    }
