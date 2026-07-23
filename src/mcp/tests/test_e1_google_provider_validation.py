# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-5 — Google/Gemini keys are validatable (CR-108 residual).

Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-108). Adversarial validation confirmed google keys were storable, env-exported,
and consumable via BYOK gemini dispatch — but absent from PROVIDER_REGISTRY and
the setup validation maps, so POST /providers/google/validate 404'd and no
endpoint could validate a stored key. RED-then-GREEN.
"""
from __future__ import annotations

import pytest


class _Resp:
    status_code = 200

    def json(self):
        return {"models": []}


class _Client:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        _Client.url = url
        _Client.headers = headers
        return _Resp()


def test_cr108_google_in_provider_registry():
    from config.providers import PROVIDER_REGISTRY

    assert "google" in PROVIDER_REGISTRY
    assert PROVIDER_REGISTRY["google"]["env_var"] == "GOOGLE_API_KEY"
    assert PROVIDER_REGISTRY["google"]["requires_api_key"] is True


def test_cr108_google_in_setup_maps():
    # The __env__ resolution map and the fallback url_map both include google.
    import inspect

    from app.routers import setup

    src = inspect.getsource(setup)
    assert '"google": "GOOGLE_API_KEY"' in src
    assert "generativelanguage.googleapis.com" in src


@pytest.mark.asyncio
async def test_cr108_google_validate_uses_gemini_auth(monkeypatch):
    """validate_provider_key('google', ...) must attempt validation against the
    Gemini endpoint with an x-goog-api-key header — not 404 as 'Unknown provider'."""
    from config import providers

    monkeypatch.setattr(providers.httpx, "AsyncClient", _Client)

    ok, msg = await providers.validate_provider_key("google", "test-key-123")

    assert ok is True, msg
    assert "generativelanguage.googleapis.com" in _Client.url
    assert _Client.headers == {"x-goog-api-key": "test-key-123"}
