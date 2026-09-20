# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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


def test_patch_is_visible_to_the_retrieval_reader(client, monkeypatch):
    """The only runtime reader is ``utils.domain_privacy.sensitive_domains_opted_in()``,
    which reads the ``config.settings`` submodule plane. A PATCH that only
    mutates the ``config`` package plane returns 200 and changes nothing."""
    import config.settings
    from utils.domain_privacy import sensitive_domains_opted_in

    monkeypatch.setattr(config.settings, "SENSITIVE_DOMAIN_RETRIEVAL_ENABLED", False)
    assert sensitive_domains_opted_in() is False

    r = client.patch("/settings", json={"sensitive_domain_retrieval": True})
    assert r.status_code == 200
    assert sensitive_domains_opted_in() is True

    r = client.patch("/settings", json={"sensitive_domain_retrieval": False})
    assert r.status_code == 200
    assert sensitive_domains_opted_in() is False


def test_get_reports_the_plane_retrieval_reads(client, monkeypatch):
    """GET must echo the plane that gates retrieval, not a shadow copy."""
    import config
    import config.settings

    monkeypatch.setattr(config.settings, "SENSITIVE_DOMAIN_RETRIEVAL_ENABLED", True)
    monkeypatch.setattr(config, "SENSITIVE_DOMAIN_RETRIEVAL_ENABLED", False, raising=False)
    assert client.get("/settings").json()["sensitive_domain_retrieval"] is True
