# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Internal-only trading agent endpoints (stripped from public distribution)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.deps import get_chroma, get_neo4j
from app.models.trading import (
    CascadeConfirmRequest,
    HerdDetectRequest,
    KellySizeRequest,
    LongshotSurfaceRequest,
    TradingSignalRequest,
)

logger = logging.getLogger("ai-companion")


def register_trading_endpoints(router: APIRouter) -> None:
    """Register trading POST endpoints on *router* if trading is enabled."""
    import config

    if not config.settings.CERID_TRADING_ENABLED:
        return

    @router.post("/agent/trading/signal")
    async def trading_signal_endpoint(req: TradingSignalRequest):
        """Enrich a trading signal with KB context."""
        try:
            from agents.trading_agent import trading_signal_enrich
            return await trading_signal_enrich(
                query=req.query,
                signal_data=req.signal_data,
                domains=req.domains,
                chroma=get_chroma(),
                neo4j=get_neo4j(),
                top_k=req.top_k,
            )
        except Exception as e:
            logger.error(f"Trading signal enrich error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/agent/trading/herd-detect")
    async def trading_herd_detect(req: HerdDetectRequest):
        """Detect herd behavior via correlation graph violations."""
        try:
            from agents.trading_agent import herd_detect
            return await herd_detect(
                asset=req.asset,
                sentiment_data=req.sentiment_data,
                neo4j=get_neo4j(),
            )
        except Exception as e:
            logger.error(f"Herd detect error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/agent/trading/kelly-size")
    async def trading_kelly_size(req: KellySizeRequest):
        """Query historical CV_edge for Kelly sizing."""
        try:
            from agents.trading_agent import kelly_size
            return await kelly_size(
                strategy=req.strategy,
                confidence=req.confidence,
                win_loss_ratio=req.win_loss_ratio,
                neo4j=get_neo4j(),
            )
        except Exception as e:
            logger.error(f"Kelly size error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/agent/trading/cascade-confirm")
    async def trading_cascade_confirm(req: CascadeConfirmRequest):
        """Confirm cascade pattern against historical data."""
        try:
            from agents.trading_agent import cascade_confirm
            return await cascade_confirm(
                asset=req.asset,
                liquidation_events=req.liquidation_events,
                neo4j=get_neo4j(),
            )
        except Exception as e:
            logger.error(f"Cascade confirm error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/agent/trading/longshot-surface")
    async def trading_longshot_surface(req: LongshotSurfaceRequest):
        """Query stored calibration surface from Neo4j."""
        try:
            from agents.trading_agent import longshot_surface_query
            return await longshot_surface_query(
                asset=req.asset,
                date_range=req.date_range,
                neo4j=get_neo4j(),
            )
        except Exception as e:
            logger.error(f"Longshot surface error: {e}")
            raise HTTPException(status_code=500, detail=str(e))
