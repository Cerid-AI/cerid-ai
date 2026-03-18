# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stable SDK endpoints for external cerid-series consumers.

These thin facades delegate to the existing agent/health endpoints,
providing a versioned contract (``/sdk/v1/``) that survives internal
refactoring of the ``/agent/`` paths.

Consumers should send ``X-Client-ID`` to get per-client rate limiting.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from models.trading import (
    CascadeConfirmRequest,
    HerdDetectRequest,
    KellySizeRequest,
    LongshotSurfaceRequest,
    TradingSignalRequest,
)
from routers.agents import (
    trading_cascade_confirm_endpoint,
    trading_herd_detect_endpoint,
    trading_kelly_size_endpoint,
    trading_longshot_surface_endpoint,
    trading_signal_endpoint,
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


@router.post("/query")
async def sdk_query(req: AgentQueryRequest, request: Request):
    """KB query with reranking — stable contract for external consumers."""
    return await agent_query_endpoint(req, request)


@router.post("/hallucination")
async def sdk_hallucination(req: HallucinationCheckRequest):
    """Hallucination detection — stable contract for external consumers."""
    return await hallucination_check_endpoint(req)


@router.post("/memory/extract")
async def sdk_memory_extract(req: MemoryExtractionRequest):
    """Memory extraction — stable contract for external consumers."""
    return await memory_extract_endpoint(req)


@router.get("/health")
def sdk_health():
    """Health check with version and feature flags."""
    from config.features import FEATURE_TOGGLES

    base = health_check()
    base["version"] = _app_version()
    # Expose only toggles relevant to external consumers
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


def _app_version() -> str:
    """Read version from the FastAPI app metadata."""
    return "1.0.0"


# ---------------------------------------------------------------------------
# Trading SDK facades (cerid-trading-agent stable contract)
# ---------------------------------------------------------------------------

@router.post("/trading/signal")
async def sdk_trading_signal(req: TradingSignalRequest):
    """Trading signal enrichment — stable contract for cerid-trading-agent."""
    return await trading_signal_endpoint(req)


@router.post("/trading/herd-detect")
async def sdk_trading_herd_detect(req: HerdDetectRequest):
    """Herd behavior detection — stable contract for cerid-trading-agent."""
    return await trading_herd_detect_endpoint(req)


@router.post("/trading/kelly-size")
async def sdk_trading_kelly_size(req: KellySizeRequest):
    """Kelly sizing — stable contract for cerid-trading-agent."""
    return await trading_kelly_size_endpoint(req)


@router.post("/trading/cascade-confirm")
async def sdk_trading_cascade_confirm(req: CascadeConfirmRequest):
    """Cascade confirmation — stable contract for cerid-trading-agent."""
    return await trading_cascade_confirm_endpoint(req)


@router.post("/trading/longshot-surface")
async def sdk_trading_longshot_surface(req: LongshotSurfaceRequest):
    """Longshot surface query — stable contract for cerid-trading-agent."""
    return await trading_longshot_surface_endpoint(req)
