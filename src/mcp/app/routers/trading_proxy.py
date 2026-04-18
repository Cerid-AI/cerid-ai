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

from core.utils.circuit_breaker import (
    _BREAKER_REGISTRY,
    AsyncCircuitBreaker,
    CircuitOpenError,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/trading", tags=["trading-proxy"])

# Register the breaker lazily here (rather than in core/utils/circuit_breaker.py)
# so the trading-specific wiring lives entirely inside the internal-only
# trading_proxy module. The public repo never imports this file, so the
# registry stays provider-neutral there. setdefault makes import idempotent
# even if something else adds a breaker with the same name first.
_trading_breaker = _BREAKER_REGISTRY.setdefault(
    "trading-agent",
    AsyncCircuitBreaker("trading-agent", failure_threshold=3, recovery_timeout=30),
)

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


async def _proxy_get(path: str, **log_ctx: Any) -> Any:
    """GET `path` on the trading agent through the circuit breaker.

    Returns parsed JSON on success. Translates HTTP 5xx / network / circuit-open
    errors into the appropriate FastAPI HTTPException so callers only need to
    shape the happy-path response. 4xx is surfaced verbatim and does not trip
    the breaker (handled by _is_client_error in circuit_breaker.py).
    """
    client = _get_client()

    async def _call() -> httpx.Response:
        resp = await client.get(path)
        resp.raise_for_status()
        return resp

    try:
        resp = await _trading_breaker.call(_call)
        return resp.json()
    except httpx.HTTPStatusError as e:
        logger.warning("trading_proxy_http_error", path=path, status=e.response.status_code, **log_ctx)
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.warning("trading_proxy_unreachable", path=path, error=str(e), **log_ctx)
        raise HTTPException(status_code=502, detail=f"Trading agent unreachable: {e}")
    except CircuitOpenError as e:
        logger.warning("trading_proxy_circuit_open", path=path, retry_after=e.retry_after, **log_ctx)
        raise HTTPException(
            status_code=503,
            detail=f"Trading agent circuit open, retry in {e.retry_after:.0f}s",
            headers={"Retry-After": str(max(1, int(e.retry_after) + 1))},
        )


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
    data = await _proxy_get("/sessions")
    return [TradingSession(**s) if isinstance(s, dict) else TradingSession(name=str(s)) for s in data]


@router.get("/sessions/{name}/portfolio")
async def proxy_session_portfolio(name: str) -> PortfolioResponse:
    """Proxy session P&L and holdings from trading agent."""
    data = await _proxy_get(f"/sessions/{name}/portfolio", session=name)
    return PortfolioResponse(**data)


@router.get("/sessions/{name}/positions")
async def proxy_session_positions(name: str) -> list[PositionItem]:
    """Proxy open positions from a trading session."""
    data = await _proxy_get(f"/sessions/{name}/positions", session=name)
    return [PositionItem(**p) if isinstance(p, dict) else PositionItem() for p in data]


@router.get("/sessions/{name}/signals")
async def proxy_session_signals(name: str) -> list[SignalItem]:
    """Proxy recent trading signals from a session."""
    data = await _proxy_get(f"/sessions/{name}/signals", session=name)
    return [SignalItem(**s) if isinstance(s, dict) else SignalItem() for s in data]


@router.get("/aggregate/portfolio")
async def proxy_aggregate_portfolio() -> PortfolioResponse:
    """Proxy cross-session aggregate portfolio from trading agent."""
    return PortfolioResponse(**await _proxy_get("/aggregate/portfolio"))


@router.get("/market-data")
async def proxy_market_data() -> MarketDataResponse:
    """Proxy live market data (prices, VPIN, candles) from trading agent."""
    return MarketDataResponse(**await _proxy_get("/market-data"))
