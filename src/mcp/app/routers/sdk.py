# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stable SDK endpoints for external cerid-series consumers.

These thin facades delegate to the existing agent/health endpoints,
providing a versioned contract (``/sdk/v1/``) that survives internal
refactoring of the ``/agent/`` paths.

Consumers should send ``X-Client-ID`` for per-client rate limiting and
domain access control.  See ``config.settings.CONSUMER_REGISTRY`` for
the per-consumer configuration and ``docs/INTEGRATION_GUIDE.md`` for
adding new cerid-series consumers.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.models.sdk import (
    SDKHallucinationResponse,
    SDKHealthResponse,
    SDKMemoryExtractResponse,
    SDKQueryResponse,
)
from app.routers.agents import (
    AgentQueryRequest,
    HallucinationCheckRequest,
    MemoryExtractionRequest,
    agent_query_endpoint,
    hallucination_check_endpoint,
    memory_extract_endpoint,
)
from app.routers.health import degradation_status, health_check, list_collections
from app.routers.plugins import list_plugins
from app.routers.query import query_knowledge
from app.services.ingestion import ingest_content, ingest_file
from config.features import FEATURE_FLAGS, FEATURE_TIER
from config.taxonomy import DOMAINS, TAXONOMY

router = APIRouter(prefix="/sdk/v1", tags=["SDK"])

_VERSION = "0.82.0"  # Phase 41: typed response models, consumer domain isolation

_503 = {"description": "One or more backend services unavailable"}
_422 = {"description": "Invalid request parameters"}


class SDKIngestRequest(BaseModel):
    content: str
    domain: str = "general"
    tags: str = ""


class SDKIngestFileRequest(BaseModel):
    file_path: str
    domain: str = ""
    tags: str = ""


class SDKSearchRequest(BaseModel):
    query: str
    domain: str = "general"
    top_k: int = Field(3, ge=1, le=20)


# ---------------------------------------------------------------------------
# Core endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/query",
    response_model=SDKQueryResponse,
    summary="KB Query",
    description="Multi-domain knowledge base search with hybrid BM25+vector retrieval and optional LLM reranking. "
    "Results are scoped by the consumer's allowed_domains in CONSUMER_REGISTRY.",
    responses={422: _422, 503: _503},
)
async def sdk_query(req: AgentQueryRequest, request: Request):
    return await agent_query_endpoint(req, request)


@router.post(
    "/hallucination",
    response_model=SDKHallucinationResponse,
    summary="Hallucination Detection",
    description="Verify factual claims in a response against the KB. Returns per-claim status "
    "(verified/unverified/uncertain) with sources and confidence scores.",
    responses={422: _422, 503: _503},
)
async def sdk_hallucination(req: HallucinationCheckRequest):
    return await hallucination_check_endpoint(req)


@router.post(
    "/memory/extract",
    response_model=SDKMemoryExtractResponse,
    summary="Memory Extraction",
    description="Extract facts, decisions, and preferences from conversation text and store as KB artifacts. "
    "Deduplicates against existing memories automatically.",
    responses={422: _422, 503: _503},
)
async def sdk_memory_extract(req: MemoryExtractionRequest):
    return await memory_extract_endpoint(req)


@router.get(
    "/health",
    response_model=SDKHealthResponse,
    summary="Health Check",
    description="Service connectivity and consumer-relevant feature flags. "
    "Returns 'healthy' when all services are connected, 'degraded' otherwise.",
)
def sdk_health():
    from config.features import FEATURE_TOGGLES

    base = health_check()
    base["version"] = _VERSION
    base["features"] = {
        k: v for k, v in FEATURE_TOGGLES.items()
        if k in (
            "enable_hallucination_check",
            "enable_feedback_loop",
            "enable_self_rag",
            "enable_memory_extraction",
        )
    }
    return base


# ---------------------------------------------------------------------------
# Ingest endpoints
# ---------------------------------------------------------------------------


@router.post("/ingest", summary="Ingest Text", responses={422: _422, 503: _503})
async def sdk_ingest(req: SDKIngestRequest):
    import asyncio
    result = await asyncio.to_thread(
        ingest_content,
        req.content,
        domain=req.domain,
        metadata={"tags": req.tags},
    )
    return result


@router.post("/ingest/file", summary="Ingest File", responses={422: _422, 503: _503})
async def sdk_ingest_file(req: SDKIngestFileRequest):
    result = await ingest_file(
        req.file_path,
        domain=req.domain,
        tags=req.tags,
    )
    return result


# ---------------------------------------------------------------------------
# Collections / Taxonomy / Search
# ---------------------------------------------------------------------------


@router.get("/collections", summary="List Collections")
def sdk_collections():
    return list_collections()


@router.get("/taxonomy", summary="Domain Taxonomy")
def sdk_taxonomy():
    return {"domains": list(DOMAINS), "taxonomy": dict(TAXONOMY)}


@router.get("/health/detailed", summary="Detailed Health")
def sdk_health_detailed():
    return degradation_status()


@router.get("/settings", summary="SDK Settings")
def sdk_settings():
    return {"version": _VERSION, "tier": FEATURE_TIER, "features": dict(FEATURE_FLAGS)}


@router.post("/search", summary="KB Search", responses={422: _422, 503: _503})
def sdk_search(req: SDKSearchRequest, request: Request):
    from config.settings import CONSUMER_REGISTRY
    client_id = request.headers.get("x-client-id", "gui")
    consumer = CONSUMER_REGISTRY.get(client_id, CONSUMER_REGISTRY.get("_default", {}))
    allowed_domains = consumer.get("allowed_domains")
    domain = req.domain
    if allowed_domains and domain not in allowed_domains:
        domain = allowed_domains[0]
    result = query_knowledge(
        req.query,
        domain=domain,
        top_k=req.top_k,
    )
    sources = result.get("sources", [])
    return {"results": sources, "total_results": len(sources), "confidence": result.get("confidence", 0.0)}


@router.get("/plugins", summary="List Plugins")
def sdk_plugins():
    result = list_plugins()
    return {"plugins": [p.model_dump() for p in result.plugins], "total": result.total}
