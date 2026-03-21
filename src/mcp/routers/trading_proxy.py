# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""FastAPI proxy routes for trading agent dashboard.

Proxies /api/trading/* to TRADING_AGENT_URL. Gated by CERID_TRADING_ENABLED.
This avoids CORS issues — the browser talks only to cerid-ai.
"""
from __future__ import annotations

from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/trading", tags=["trading-proxy"])


def _get_trading_url() -> str:
    """Get trading agent URL from config."""
    from config.settings import TRADING_AGENT_URL
    return TRADING_AGENT_URL


@router.get("/sessions")
async def proxy_sessions() -> Any:
    """Proxy session list from trading agent."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_get_trading_url()}/sessions")
            resp.raise_for_status()
            return resp.json()
    except Exception:
        logger.warning("trading_proxy_sessions_failed", exc_info=True)
        raise HTTPException(status_code=502, detail="Trading agent unavailable")


@router.get("/sessions/{name}/portfolio")
async def proxy_session_portfolio(name: str) -> Any:
    """Proxy session P&L and holdings from trading agent."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_get_trading_url()}/sessions/{name}/portfolio")
            resp.raise_for_status()
            return resp.json()
    except Exception:
        logger.warning("trading_proxy_portfolio_failed", session=name, exc_info=True)
        raise HTTPException(status_code=502, detail="Trading agent unavailable")


@router.get("/sessions/{name}/positions")
async def proxy_session_positions(name: str) -> Any:
    """Proxy open positions from a trading session."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_get_trading_url()}/sessions/{name}/positions")
            resp.raise_for_status()
            return resp.json()
    except Exception:
        logger.warning("trading_proxy_positions_failed", session=name, exc_info=True)
        raise HTTPException(status_code=502, detail="Trading agent unavailable")


@router.get("/sessions/{name}/signals")
async def proxy_session_signals(name: str) -> Any:
    """Proxy recent trading signals from a session."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_get_trading_url()}/sessions/{name}/signals")
            resp.raise_for_status()
            return resp.json()
    except Exception:
        logger.warning("trading_proxy_signals_failed", session=name, exc_info=True)
        raise HTTPException(status_code=502, detail="Trading agent unavailable")


@router.get("/aggregate/portfolio")
async def proxy_aggregate_portfolio() -> Any:
    """Proxy cross-session aggregate portfolio from trading agent."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_get_trading_url()}/aggregate/portfolio")
            resp.raise_for_status()
            return resp.json()
    except Exception:
        logger.warning("trading_proxy_aggregate_failed", exc_info=True)
        raise HTTPException(status_code=502, detail="Trading agent unavailable")


@router.get("/market-data")
async def proxy_market_data() -> Any:
    """Proxy live market data (prices, VPIN, candles) from trading agent."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_get_trading_url()}/market-data")
            resp.raise_for_status()
            return resp.json()
    except Exception:
        logger.warning("trading_proxy_market_data_failed", exc_info=True)
        raise HTTPException(status_code=502, detail="Trading agent unavailable")
