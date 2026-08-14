# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the shared httpx.AsyncClient used by the OpenRouter credit probe.

Verifies Task 21 / audit C-8: GUI polls /providers/credits at 15s+60s cadences,
so we hoist a module-level httpx.AsyncClient instead of spawning one per call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_shared_client():
    """Reset the module-level client + credits cache between tests."""
    from app.routers import providers as providers_mod
    providers_mod._openrouter_http_client = None
    providers_mod._credits_cache = None
    yield
    providers_mod._openrouter_http_client = None
    providers_mod._credits_cache = None


@pytest.mark.asyncio
async def test_credit_probe_reuses_single_client_across_calls(monkeypatch):
    """Three sequential probe calls must share ONE httpx.AsyncClient instance."""
    from app.routers import providers as providers_mod

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123")

    created_clients: list[MagicMock] = []

    def _fake_client_factory(**kwargs):
        client = MagicMock()
        client.is_closed = False
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {}}
        client.get = AsyncMock(return_value=mock_resp)
        client.aclose = AsyncMock()
        created_clients.append(client)
        return client

    with patch.object(providers_mod.httpx, "AsyncClient", side_effect=_fake_client_factory):
        # Call the credit probe three times in sequence.
        await providers_mod.get_provider_credits()
        await providers_mod.get_provider_credits()
        await providers_mod.get_provider_credits()

    # Only one client should have been constructed, and only the FIRST call
    # goes upstream (2 GETs: /credits + /auth/key) — the second and third are
    # served from the 60s result cache (UX-10: the GUI polls at 15s+60s per
    # tab, and every poll used to hit OpenRouter).
    assert len(created_clients) == 1, (
        f"Expected 1 shared httpx.AsyncClient across 3 probe calls, "
        f"got {len(created_clients)}"
    )
    assert created_clients[0].get.await_count == 2


@pytest.mark.asyncio
async def test_credit_probe_no_key_returns_unconfigured(monkeypatch):
    """Missing OPENROUTER_API_KEY should short-circuit without touching the client."""
    from app.routers import providers as providers_mod

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    created_clients: list = []

    def _fake_client_factory(**kwargs):
        created_clients.append(kwargs)
        return MagicMock()

    with patch.object(providers_mod.httpx, "AsyncClient", side_effect=_fake_client_factory):
        result = await providers_mod.get_provider_credits()

    assert result["configured"] is False
    assert created_clients == []


@pytest.mark.asyncio
async def test_close_openrouter_client_tears_down_shared_instance():
    """close_openrouter_client should aclose the shared client and null the slot."""
    from app.routers import providers as providers_mod

    mock_client = MagicMock()
    mock_client.is_closed = False
    mock_client.aclose = AsyncMock()

    with patch.object(providers_mod.httpx, "AsyncClient", return_value=mock_client):
        client = await providers_mod._openrouter_client()
        assert client is mock_client
        assert providers_mod._openrouter_http_client is mock_client

        await providers_mod.close_openrouter_client()

    mock_client.aclose.assert_awaited_once()
    assert providers_mod._openrouter_http_client is None


@pytest.mark.asyncio
async def test_status_set_when_auth_key_fails_but_credits_ok(monkeypatch):
    """CH-CREDITS: /credits 200 but /auth/key non-200 must still set status.

    Previously `status` was assigned only inside the /auth/key 200 block, so
    a partial-success path left the field undefined. Status must derive from
    balance unconditionally.
    """
    from app.routers import providers as providers_mod

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123")

    def _fake_client_factory(**kwargs):
        client = MagicMock()
        client.is_closed = False

        async def _get(url, **kw):
            resp = MagicMock()
            if url.endswith("/credits"):
                resp.status_code = 200
                resp.json.return_value = {
                    "data": {"total_credits": 10.0, "total_usage": 2.0}
                }
            else:  # /auth/key fails
                resp.status_code = 500
                resp.json.return_value = {}
            return resp

        client.get = AsyncMock(side_effect=_get)
        client.aclose = AsyncMock()
        return client

    with patch.object(providers_mod.httpx, "AsyncClient", side_effect=_fake_client_factory):
        result = await providers_mod.get_provider_credits()

    assert result["balance"] == 8.0
    assert "status" in result
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_status_exhausted_when_balance_zero_and_auth_key_fails(monkeypatch):
    """Zero balance + /auth/key failure must still report 'exhausted'."""
    from app.routers import providers as providers_mod

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123")

    def _fake_client_factory(**kwargs):
        client = MagicMock()
        client.is_closed = False

        async def _get(url, **kw):
            resp = MagicMock()
            if url.endswith("/credits"):
                resp.status_code = 200
                resp.json.return_value = {
                    "data": {"total_credits": 5.0, "total_usage": 5.0}
                }
            else:
                resp.status_code = 503
                resp.json.return_value = {}
            return resp

        client.get = AsyncMock(side_effect=_get)
        client.aclose = AsyncMock()
        return client

    with patch.object(providers_mod.httpx, "AsyncClient", side_effect=_fake_client_factory):
        result = await providers_mod.get_provider_credits()

    assert result["balance"] == 0.0
    assert result["status"] == "exhausted"


@pytest.mark.asyncio
async def test_credits_cache_expires_after_ttl(monkeypatch):
    """UX-10: within the TTL the cached result is served; past it, refetch."""
    from app.routers import providers as providers_mod

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123")

    fake_now = [1000.0]
    monkeypatch.setattr(providers_mod.time, "monotonic", lambda: fake_now[0])

    def _fake_client_factory(**kwargs):
        client = MagicMock()
        client.is_closed = False
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": {"total_credits": 10.0, "total_usage": 1.0}}
        client.get = AsyncMock(return_value=resp)
        client.aclose = AsyncMock()
        return client

    with patch.object(providers_mod.httpx, "AsyncClient", side_effect=_fake_client_factory):
        first = await providers_mod.get_provider_credits()
        cached = await providers_mod.get_provider_credits()
        fake_now[0] += providers_mod._CREDITS_CACHE_TTL_SECONDS + 1
        refreshed = await providers_mod.get_provider_credits()

    assert first["balance"] == 9.0
    assert cached is first  # served from cache, no upstream call
    assert refreshed["balance"] == 9.0
    # 2 upstream probes total (first + post-TTL), 2 GETs each.
    client = providers_mod._openrouter_http_client
    assert client.get.await_count == 4


@pytest.mark.asyncio
async def test_missing_key_is_never_cached(monkeypatch):
    """Configuring a key must take effect immediately, not after a TTL."""
    from app.routers import providers as providers_mod

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = await providers_mod.get_provider_credits()
    assert result["configured"] is False

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-123")

    def _fake_client_factory(**kwargs):
        client = MagicMock()
        client.is_closed = False
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": {"total_credits": 3.0, "total_usage": 0.0}}
        client.get = AsyncMock(return_value=resp)
        client.aclose = AsyncMock()
        return client

    with patch.object(providers_mod.httpx, "AsyncClient", side_effect=_fake_client_factory):
        result = await providers_mod.get_provider_credits()

    assert result["configured"] is True
    assert result["balance"] == 3.0
