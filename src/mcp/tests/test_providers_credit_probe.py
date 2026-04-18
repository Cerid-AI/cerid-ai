# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the shared httpx.AsyncClient used by the OpenRouter credit probe.

Verifies Task 21 / audit C-8: GUI polls /providers/credits at 15s+60s cadences,
so we hoist a module-level httpx.AsyncClient instead of spawning one per call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_shared_client():
    """Reset the module-level client between tests."""
    from app.routers import providers as providers_mod
    providers_mod._openrouter_http_client = None
    yield
    providers_mod._openrouter_http_client = None


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

    # Only one client should have been constructed; the 6 GETs (2 per call) all
    # share it.
    assert len(created_clients) == 1, (
        f"Expected 1 shared httpx.AsyncClient across 3 probe calls, "
        f"got {len(created_clients)}"
    )
    assert created_clients[0].get.await_count == 6


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
