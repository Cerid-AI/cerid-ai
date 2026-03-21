# Copyright (c) 2026 Justin Michaels. All rights reserved.
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

from fastapi import APIRouter, HTTPException, Request

from config.settings import CERID_BOARDROOM_ENABLED, CERID_TRADING_ENABLED
from models.sdk import (
    SDKCascadeConfirmResponse,
    SDKHallucinationResponse,
    SDKHealthResponse,
    SDKHerdDetectResponse,
    SDKKellySizeResponse,
    SDKLongshotSurfaceResponse,
    SDKMemoryExtractResponse,
    SDKQueryResponse,
    SDKTradingSignalResponse,
)
from models.trading import (
    CascadeConfirmRequest,
    HerdDetectRequest,
    KellySizeRequest,
    LongshotSurfaceRequest,
    TradingSignalRequest,
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

_VERSION = "1.1.0"  # Phase 41: typed response models, consumer domain isolation

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
    return base


# ---------------------------------------------------------------------------
# Trading endpoints — gated by CERID_TRADING_ENABLED
# ---------------------------------------------------------------------------


def _require_trading() -> None:
    """Raise 404 if trading integration is disabled."""
    if not CERID_TRADING_ENABLED:
        raise HTTPException(status_code=404, detail="Trading integration disabled")


if CERID_TRADING_ENABLED:
    from routers.agents import (
        trading_cascade_confirm_endpoint,
        trading_herd_detect_endpoint,
        trading_kelly_size_endpoint,
        trading_longshot_surface_endpoint,
        trading_signal_endpoint,
    )

    @router.post(
        "/trading/signal",
        response_model=SDKTradingSignalResponse,
        summary="Trading Signal Enrichment",
        description="Enrich a trading signal with KB context — historical trades, domain knowledge, and confidence scoring.",
        responses={422: _422, 503: _503},
    )
    async def sdk_trading_signal(req: TradingSignalRequest):
        return await trading_signal_endpoint(req)

    @router.post(
        "/trading/herd-detect",
        response_model=SDKHerdDetectResponse,
        summary="Herd Behavior Detection",
        description="Detect herd behavior patterns by analyzing correlation graph violations and historical herd events.",
        responses={422: _422, 503: _503},
    )
    async def sdk_trading_herd_detect(req: HerdDetectRequest):
        return await trading_herd_detect_endpoint(req)

    @router.post(
        "/trading/kelly-size",
        response_model=SDKKellySizeResponse,
        summary="Kelly Criterion Sizing",
        description="Compute Kelly fraction for position sizing using historical win/loss data from KB.",
        responses={422: _422, 503: _503},
    )
    async def sdk_trading_kelly_size(req: KellySizeRequest):
        return await trading_kelly_size_endpoint(req)

    @router.post(
        "/trading/cascade-confirm",
        response_model=SDKCascadeConfirmResponse,
        summary="Cascade Pattern Confirmation",
        description="Confirm whether a liquidation cascade pattern matches historical cascade events in the KB.",
        responses={422: _422, 503: _503},
    )
    async def sdk_trading_cascade_confirm(req: CascadeConfirmRequest):
        return await trading_cascade_confirm_endpoint(req)

    @router.post(
        "/trading/longshot-surface",
        response_model=SDKLongshotSurfaceResponse,
        summary="Longshot Calibration Surface",
        description="Query the calibration surface for longshot probability estimates from historical prediction market data.",
        responses={422: _422, 503: _503},
    )
    async def sdk_trading_longshot_surface(req: LongshotSurfaceRequest):
        return await trading_longshot_surface_endpoint(req)


# ---------------------------------------------------------------------------
# Boardroom endpoints — gated by CERID_BOARDROOM_ENABLED
# ---------------------------------------------------------------------------


def _require_boardroom() -> None:
    """Raise 404 if boardroom integration is disabled."""
    if not CERID_BOARDROOM_ENABLED:
        raise HTTPException(status_code=404, detail="Boardroom integration disabled")


if CERID_BOARDROOM_ENABLED:

    @router.get(
        "/ops/health",
        summary="Boardroom Health Check",
        description="Check boardroom integration status and tier.",
    )
    async def sdk_ops_health():
        from config.settings import CERID_BOARDROOM_TIER

        _require_boardroom()
        return {
            "status": "ok",
            "boardroom_enabled": True,
            "tier": CERID_BOARDROOM_TIER,
            "domains": ["strategy", "competitive_intel", "marketing", "advertising",
                        "finance", "operations", "audit"],
        }

    @router.post(
        "/ops/competitive-scan",
        summary="Competitive Intelligence Scan",
        description="Run a structured competitive analysis using KB + web search.",
        responses={422: _422, 503: _503},
    )
    async def sdk_ops_competitive_scan(req: AgentQueryRequest, request: Request):
        _require_boardroom()
        req.domains = ["competitive_intel"]
        result = await agent_query_endpoint(req, request)
        return {"result": result, "domain": "competitive_intel"}

    @router.post(
        "/ops/strategy-brief",
        summary="Strategy Brief Generation",
        description="Generate a board-ready strategy brief from accumulated intel.",
        responses={422: _422, 503: _503},
    )
    async def sdk_ops_strategy_brief(req: AgentQueryRequest, request: Request):
        _require_boardroom()
        req.domains = ["strategy", "competitive_intel"]
        result = await agent_query_endpoint(req, request)
        return {"result": result, "domains": ["strategy", "competitive_intel"]}

    @router.get(
        "/ops/governance-log",
        summary="Governance Audit Log",
        description="Query the boardroom audit trail for agent actions and approvals.",
    )
    async def sdk_ops_governance_log():
        _require_boardroom()
        # Placeholder — will query audit domain in KB
        return {"entries": [], "total": 0}
