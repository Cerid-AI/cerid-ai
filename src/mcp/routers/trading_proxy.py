# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""FastAPI proxy routes for trading agent dashboard.

Proxies /api/trading/* to TRADING_AGENT_URL. Gated by CERID_TRADING_ENABLED.
This avoids CORS issues — the browser talks only to cerid-ai.

Uses a module-level singleton httpx.AsyncClient with connection pooling
to avoid per-request connection overhead.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/trading", tags=["trading-proxy"])

# ---------------------------------------------------------------------------
# Connection-pooled singleton client
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return a singleton httpx client with connection pooling."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=os.getenv("TRADING_AGENT_URL", "http://localhost:8090"),
            timeout=10.0,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _client


async def close_trading_proxy_client() -> None:
    """Gracefully close the singleton httpx client (called during shutdown)."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class TradingSession(BaseModel):
    name: str
    status: str = ""
    strategy: str = ""
    pnl: float = 0.0


class PortfolioResponse(BaseModel):
    total_pnl: float = 0.0
    total_value: float = 0.0
    positions: list[dict[str, Any]] = Field(default_factory=list)


class PositionItem(BaseModel):
    symbol: str = ""
    side: str = ""
    size: float = 0.0
    entry_price: float = 0.0
    unrealized_pnl: float = 0.0


class SignalItem(BaseModel):
    timestamp: str = ""
    symbol: str = ""
    direction: str = ""
    confidence: float = 0.0
    strategy: str = ""


class MarketDataResponse(BaseModel):
    prices: dict[str, Any] = Field(default_factory=dict)
    vpin: dict[str, Any] = Field(default_factory=dict)
    candles: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/sessions")
async def proxy_sessions() -> list[TradingSession]:
    """Proxy session list from trading agent."""
    try:
        client = _get_client()
        resp = await client.get("/sessions")
        resp.raise_for_status()
        data = resp.json()
        return [TradingSession(**s) if isinstance(s, dict) else TradingSession(name=str(s)) for s in data]
    except httpx.HTTPStatusError as e:
        logger.warning("trading_proxy_sessions_failed", status=e.response.status_code)
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning("trading_proxy_sessions_unreachable", error=str(e))
        raise HTTPException(status_code=502, detail=f"Trading agent unreachable: {e}")


@router.get("/sessions/{name}/portfolio")
async def proxy_session_portfolio(name: str) -> PortfolioResponse:
    """Proxy session P&L and holdings from trading agent."""
    try:
        client = _get_client()
        resp = await client.get(f"/sessions/{name}/portfolio")
        resp.raise_for_status()
        return PortfolioResponse(**resp.json())
    except httpx.HTTPStatusError as e:
        logger.warning("trading_proxy_portfolio_failed", session=name, status=e.response.status_code)
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning("trading_proxy_portfolio_unreachable", session=name, error=str(e))
        raise HTTPException(status_code=502, detail=f"Trading agent unreachable: {e}")


@router.get("/sessions/{name}/positions")
async def proxy_session_positions(name: str) -> list[PositionItem]:
    """Proxy open positions from a trading session."""
    try:
        client = _get_client()
        resp = await client.get(f"/sessions/{name}/positions")
        resp.raise_for_status()
        data = resp.json()
        return [PositionItem(**p) if isinstance(p, dict) else PositionItem() for p in data]
    except httpx.HTTPStatusError as e:
        logger.warning("trading_proxy_positions_failed", session=name, status=e.response.status_code)
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning("trading_proxy_positions_unreachable", session=name, error=str(e))
        raise HTTPException(status_code=502, detail=f"Trading agent unreachable: {e}")


@router.get("/sessions/{name}/signals")
async def proxy_session_signals(name: str) -> list[SignalItem]:
    """Proxy recent trading signals from a session."""
    try:
        client = _get_client()
        resp = await client.get(f"/sessions/{name}/signals")
        resp.raise_for_status()
        data = resp.json()
        return [SignalItem(**s) if isinstance(s, dict) else SignalItem() for s in data]
    except httpx.HTTPStatusError as e:
        logger.warning("trading_proxy_signals_failed", session=name, status=e.response.status_code)
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning("trading_proxy_signals_unreachable", session=name, error=str(e))
        raise HTTPException(status_code=502, detail=f"Trading agent unreachable: {e}")


@router.get("/aggregate/portfolio")
async def proxy_aggregate_portfolio() -> PortfolioResponse:
    """Proxy cross-session aggregate portfolio from trading agent."""
    try:
        client = _get_client()
        resp = await client.get("/aggregate/portfolio")
        resp.raise_for_status()
        return PortfolioResponse(**resp.json())
    except httpx.HTTPStatusError as e:
        logger.warning("trading_proxy_aggregate_failed", status=e.response.status_code)
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning("trading_proxy_aggregate_unreachable", error=str(e))
        raise HTTPException(status_code=502, detail=f"Trading agent unreachable: {e}")


@router.get("/market-data")
async def proxy_market_data() -> MarketDataResponse:
    """Proxy live market data (prices, VPIN, candles) from trading agent."""
    try:
        client = _get_client()
        resp = await client.get("/market-data")
        resp.raise_for_status()
        return MarketDataResponse(**resp.json())
    except httpx.HTTPStatusError as e:
        logger.warning("trading_proxy_market_data_failed", status=e.response.status_code)
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning("trading_proxy_market_data_unreachable", error=str(e))
        raise HTTPException(status_code=502, detail=f"Trading agent unreachable: {e}")
