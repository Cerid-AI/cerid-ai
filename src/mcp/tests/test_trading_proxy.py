# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Trading-proxy router tests — circuit breaker + error translation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _reset_trading_breaker() -> None:
    from app.routers.trading_proxy import _trading_breaker
    from core.utils.circuit_breaker import CircuitState

    _trading_breaker._state = CircuitState.CLOSED
    _trading_breaker._failure_count = 0
    _trading_breaker._last_failure_time = 0


def test_breaker_registered_with_expected_name():
    from app.routers.trading_proxy import _trading_breaker
    from core.utils.circuit_breaker import _BREAKER_REGISTRY

    assert "trading-agent" in _BREAKER_REGISTRY
    assert _BREAKER_REGISTRY["trading-agent"] is _trading_breaker


def test_proxy_get_returns_json_on_success():
    _reset_trading_breaker()
    from app.routers import trading_proxy

    mock_resp = MagicMock()
    mock_resp.json.return_value = [{"name": "alpha", "status": "ok"}]
    mock_resp.raise_for_status.return_value = None
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch.object(trading_proxy, "_get_client", return_value=mock_client):
        data = _run(trading_proxy._proxy_get("/sessions"))

    assert data == [{"name": "alpha", "status": "ok"}]


def test_connect_error_translates_to_502():
    _reset_trading_breaker()
    from app.routers import trading_proxy

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

    with patch.object(trading_proxy, "_get_client", return_value=mock_client):
        with pytest.raises(HTTPException) as exc:
            _run(trading_proxy._proxy_get("/sessions"))
    assert exc.value.status_code == 502


def test_http_4xx_passes_through_and_does_not_trip_breaker():
    _reset_trading_breaker()
    from app.routers import trading_proxy
    from app.routers.trading_proxy import _trading_breaker
    from core.utils.circuit_breaker import CircuitState

    # Build an httpx.HTTPStatusError with a 404 body
    req = httpx.Request("GET", "http://trading-agent:8090/nope")
    resp = httpx.Response(404, request=req)
    err = httpx.HTTPStatusError("404 Not Found", request=req, response=resp)

    raising_resp = MagicMock()
    raising_resp.raise_for_status.side_effect = err
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=raising_resp)

    with patch.object(trading_proxy, "_get_client", return_value=mock_client):
        for _ in range(5):  # well above failure_threshold
            with pytest.raises(HTTPException) as exc:
                _run(trading_proxy._proxy_get("/nope"))
            assert exc.value.status_code == 404

    # 4xx must NOT count as failure → breaker stays CLOSED
    assert _trading_breaker.state == CircuitState.CLOSED
    assert _trading_breaker._failure_count == 0


def test_5xx_trips_breaker_after_threshold_then_503():
    _reset_trading_breaker()
    from app.routers import trading_proxy
    from app.routers.trading_proxy import _trading_breaker
    from core.utils.circuit_breaker import CircuitState

    req = httpx.Request("GET", "http://trading-agent:8090/sessions")
    resp500 = httpx.Response(500, request=req)
    err500 = httpx.HTTPStatusError("500 Internal", request=req, response=resp500)

    raising_resp = MagicMock()
    raising_resp.raise_for_status.side_effect = err500
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=raising_resp)

    with patch.object(trading_proxy, "_get_client", return_value=mock_client):
        # Drive failures up to the threshold (3) — each returns 500
        for _ in range(_trading_breaker.failure_threshold):
            with pytest.raises(HTTPException) as exc:
                _run(trading_proxy._proxy_get("/sessions"))
            assert exc.value.status_code == 500

        # Breaker should now be OPEN
        assert _trading_breaker.state == CircuitState.OPEN

        # Next call short-circuits to 503 with Retry-After header
        with pytest.raises(HTTPException) as exc:
            _run(trading_proxy._proxy_get("/sessions"))
        assert exc.value.status_code == 503
        assert "Retry-After" in (exc.value.headers or {})
