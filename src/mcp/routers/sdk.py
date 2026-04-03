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

import config
from models.sdk import (
    SDKCollectionsResponse,
    SDKDetailedHealthResponse,
    SDKHallucinationResponse,
    SDKHealthResponse,
    SDKIngestFileRequest,
    SDKIngestRequest,
    SDKIngestResponse,
    SDKMemoryExtractResponse,
    SDKPluginListResponse,
    SDKQueryResponse,
    SDKSearchRequest,
    SDKSearchResponse,
    SDKSettingsResponse,
    SDKTaxonomyResponse,
)
from routers.agents import (
    AgentQueryRequest,
    HallucinationCheckRequest,
    MemoryExtractionRequest,
    agent_query_endpoint,
    hallucination_check_endpoint,
    memory_extract_endpoint,
)
from routers.health import health_check

router = APIRouter(prefix="/sdk/v1", tags=["SDK"])

_VERSION = "1.1.0"  # typed response models, consumer domain isolation

_503 = {"description": "One or more backend services unavailable"}
_422 = {"description": "Invalid request parameters"}


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
    # Expose internal LLM provider info for SDK consumers
    import os
    base["internal_llm"] = {
        "provider": config.INTERNAL_LLM_PROVIDER,
        "model": config.INTERNAL_LLM_MODEL or config.OLLAMA_DEFAULT_MODEL,
        "ollama_enabled": os.getenv("OLLAMA_ENABLED", "false").lower() in ("true", "1"),
    }
    return base


# ---------------------------------------------------------------------------
# Extended SDK endpoints (Phase 1)
# ---------------------------------------------------------------------------


@router.post(
    "/ingest",
    response_model=SDKIngestResponse,
    summary="Ingest Content",
    description="Ingest raw text content into the knowledge base. "
    "Content is chunked, embedded, and stored in ChromaDB + Neo4j.",
    responses={422: _422, 503: _503},
)
def sdk_ingest(req: SDKIngestRequest, request: Request):
    from services.ingestion import ingest_content

    client_id = request.headers.get("x-client-id", "sdk")
    result = ingest_content(
        content=req.content,
        domain=req.domain,
        metadata={"tags": req.tags, "client_source": client_id} if req.tags else {"client_source": client_id},
    )
    return SDKIngestResponse(
        status=result.get("status", "error"),
        artifact_id=result.get("artifact_id", ""),
        chunks=result.get("chunks", 0),
        domain=result.get("domain", req.domain),
    )


@router.post(
    "/ingest/file",
    response_model=SDKIngestResponse,
    summary="Ingest File",
    description="Ingest a file from the archive or an absolute path. "
    "Supports PDF, DOCX, XLSX, Markdown, code files, and 30+ formats.",
    responses={422: _422, 503: _503},
)
async def sdk_ingest_file(req: SDKIngestFileRequest, request: Request):
    from services.ingestion import ingest_file

    client_id = request.headers.get("x-client-id", "sdk")
    result = await ingest_file(
        file_path=req.file_path,
        domain=req.domain,
        tags=req.tags,
        categorize_mode=req.categorize_mode,
        client_source=client_id,
    )
    return SDKIngestResponse(
        status=result.get("status", "error"),
        artifact_id=result.get("artifact_id", ""),
        chunks=result.get("chunks", 0),
        domain=result.get("domain", ""),
    )


@router.get(
    "/collections",
    response_model=SDKCollectionsResponse,
    summary="List Collections",
    description="List all knowledge base collections (one per domain).",
)
def sdk_collections():
    from routers.health import list_collections

    result = list_collections()
    return SDKCollectionsResponse(
        collections=result.get("collections", []),
        total=result.get("total", 0),
    )


@router.get(
    "/taxonomy",
    response_model=SDKTaxonomyResponse,
    summary="Get Taxonomy",
    description="Get the domain taxonomy tree with sub-categories and tag vocabulary.",
)
def sdk_taxonomy():
    from config.taxonomy import DOMAINS, TAXONOMY

    return SDKTaxonomyResponse(
        domains=list(DOMAINS),
        taxonomy=dict(TAXONOMY),
    )


@router.get(
    "/health/detailed",
    response_model=SDKDetailedHealthResponse,
    summary="Detailed Health",
    description="Extended health check with circuit breaker states, "
    "degradation tier, and uptime.",
)
def sdk_health_detailed():
    from routers.health import degradation_status

    result = degradation_status()
    return result


@router.get(
    "/settings",
    response_model=SDKSettingsResponse,
    summary="Server Settings",
    description="Read-only server configuration: version, tier, and feature flags.",
)
def sdk_settings():
    from config.features import FEATURE_FLAGS, FEATURE_TIER

    return SDKSettingsResponse(
        version=_VERSION,
        tier=FEATURE_TIER,
        features=dict(FEATURE_FLAGS),
    )


@router.post(
    "/search",
    response_model=SDKSearchResponse,
    summary="Raw Search",
    description="Direct vector search without agent orchestration. "
    "Returns raw chunks ranked by relevance.",
    responses={422: _422, 503: _503},
)
def sdk_search(req: SDKSearchRequest):
    from routers.query import query_knowledge

    result = query_knowledge(query=req.query, domain=req.domain, top_k=req.top_k)
    return SDKSearchResponse(
        results=result.get("sources", []),
        total_results=len(result.get("sources", [])),
        confidence=result.get("confidence", 0.0),
    )


@router.get(
    "/plugins",
    response_model=SDKPluginListResponse,
    summary="List Plugins",
    description="List all loaded plugins with their status, tier, and capabilities.",
)
def sdk_plugins():
    from routers.plugins import list_plugins

    result = list_plugins()
    plugins = result.plugins if hasattr(result, "plugins") else result.get("plugins", [])
    plugins_dicts = [p.model_dump() if hasattr(p, "model_dump") else p for p in plugins]
    total = result.total if hasattr(result, "total") else result.get("total", len(plugins_dicts))
    return SDKPluginListResponse(plugins=plugins_dicts, total=total)

