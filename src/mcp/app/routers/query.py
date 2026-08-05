# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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

from fastapi import APIRouter, Request
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
    # Budget-degradation passthrough (2026-07-13): agent_query returns an
    # empty degraded envelope when the wall-clock budget expires; without
    # these fields a degraded-empty response is byte-identical to a true
    # zero-hit, so consumers (chat auto-inject, SDK, eval harnesses) could
    # not retry or discount it.
    budget_exceeded: bool = False
    degraded_reason: Any = None



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
    budget_seconds: float | None = Field(
        None, ge=1, le=120,
        description=(
            "Per-request retrieval wall-clock budget override (seconds). "
            "None = the server's interactive default (AGENT_QUERY_BUDGET_"
            "SECONDS, 20s). Mirrors /agent/query — offline/batch callers "
            "(eval harnesses, SDK batch jobs) prefer completeness over "
            "latency; without this, ambient host load degrades responses "
            "to empty envelopes that harnesses mis-score as misses."
        ),
    )
    skip_cache: bool = Field(
        False,
        description=(
            "Bypass the semantic/query caches. Mirrors /agent/query — "
            "required for A/B measurement (a cached arm silently measures "
            "the other arm's results) and fresh-data flows."
        ),
    )


@router.post("/query", response_model=QueryEndpointResponse)
async def query_endpoint(req: QueryRequest, request: Request):
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

    from app.concurrency import KB_POOL
    from app.services.request_policy import build_request_context
    from core.agents.query_agent import agent_query_full

    # E1 CR-087: resolve the caller's consumer identity so its allowed_domains /
    # strict_domains wall is applied here too — a restricted consumer must not
    # escape its allow-list by using /query instead of /agent/query.
    ctx = build_request_context(client_id=request.headers.get("x-client-id", "gui"))
    # E1 CR-096: gate under KB_POOL like /agent/query + A2A (CR-091) so unbounded
    # concurrent retrieval on /query cannot starve the /health + /observability
    # routes the pool exists to protect.
    async with KB_POOL.acquire():
        result = await agent_query_full(
            query=req.query,
            domains=[req.domain],
            top_k=req.top_k,
            exclude_packs=req.exclude_packs,
            budget_seconds=req.budget_seconds,
            skip_cache=req.skip_cache,
            external_augmentation=False,
            allowed_domains=ctx.allowed_domains_list(),
            strict_domains=ctx.strict_domains,
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
        "budget_exceeded": bool(result.get("budget_exceeded", False)),
        "degraded_reason": result.get("degraded_reason"),
    }
