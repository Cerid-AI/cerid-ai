# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 CR-004: GET /models/catalog serves the currently-dispatchable model-id set,
validated against the live OpenRouter catalog, so the FE can filter out a
delisted id (e.g. the removed grok-4.1-fast) instead of shipping a stale list.

Synthetic — the live-catalog fetch is monkeypatched, so no stack/network."""
from __future__ import annotations

import pytest

import app.routers.models as models_router


@pytest.mark.asyncio
async def test_catalog_returns_live_prefixed_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch(*_a, **_k):
        return [{"id": "x-ai/grok-4.5"}, {"id": "openai/gpt-4o-mini"}]

    monkeypatch.setattr(models_router, "fetch_openrouter_catalog", _fake_fetch)
    resp = await models_router.get_model_catalog()

    assert resp.source == "live_catalog"
    assert resp.count == 2
    # ids are openrouter/-prefixed to match the FE catalog id shape
    assert "openrouter/x-ai/grok-4.5" in resp.ids
    assert "openrouter/openai/gpt-4o-mini" in resp.ids
    # a delisted id is simply absent → the FE filters it out (can't render)
    assert "openrouter/x-ai/grok-4.1-fast" not in resp.ids


@pytest.mark.asyncio
async def test_catalog_failsoft_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty/unreachable catalog must return source='unavailable' with no ids
    so the FE shows its full catalog rather than over-filtering to nothing."""

    async def _empty(*_a, **_k):
        return []

    monkeypatch.setattr(models_router, "fetch_openrouter_catalog", _empty)
    resp = await models_router.get_model_catalog()

    assert resp.source == "unavailable"
    assert resp.ids == []
    assert resp.count == 0
