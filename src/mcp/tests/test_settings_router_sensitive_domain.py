# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Settings router GET/PATCH coverage for Task 1.2e: the dedicated
`sensitive_domain_retrieval` opt-in (independent of private_mode level)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.settings import router


@pytest.fixture
def client(monkeypatch):
    """Settings router client; forces the field to its documented default
    (False) at setup and restores it after each test — the PATCH handler
    mutates `config.SENSITIVE_DOMAIN_RETRIEVAL_ENABLED` directly."""
    import config

    monkeypatch.setattr(config, "SYNC_DIR", "", raising=False)
    monkeypatch.setattr(config, "SENSITIVE_DOMAIN_RETRIEVAL_ENABLED", False, raising=False)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_get_settings_includes_sensitive_domain_retrieval(client):
    body = client.get("/settings").json()
    assert "sensitive_domain_retrieval" in body
    assert body["sensitive_domain_retrieval"] is False


def test_patch_enables_sensitive_domain_retrieval(client):
    import config

    r = client.patch("/settings", json={"sensitive_domain_retrieval": True})
    assert r.status_code == 200
    assert r.json()["updated"] == {"sensitive_domain_retrieval": True}
    assert config.SENSITIVE_DOMAIN_RETRIEVAL_ENABLED is True


def test_patch_disables_sensitive_domain_retrieval(client):
    import config

    config.SENSITIVE_DOMAIN_RETRIEVAL_ENABLED = True  # type: ignore[assignment]
    r = client.patch("/settings", json={"sensitive_domain_retrieval": False})
    assert r.status_code == 200
    assert r.json()["updated"] == {"sensitive_domain_retrieval": False}
    assert config.SENSITIVE_DOMAIN_RETRIEVAL_ENABLED is False


def test_get_default_matches_env_default(client):
    """Safety invariant: the GET response must reflect the default-off state
    when nothing has opted in — proves the toggle starts in the safe
    direction."""
    body = client.get("/settings").json()
    assert body["sensitive_domain_retrieval"] is False
