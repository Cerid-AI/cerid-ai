# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Settings router PATCH coverage for internal_llm_provider/model.

v0.93.9 closes a GET/PATCH asymmetry — internal_llm_provider was in the
GET response but not in the PATCH schema, so operators could only flip
the provider via container env restart. These tests pin the new mutate
path so future refactors don't regress it.
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.settings import router


@pytest.fixture
def client(monkeypatch):
    """Build a TestClient with isolated env + config state.

    The settings PATCH handler mutates `os.environ` directly so changes
    take effect in-process without a worker restart. Pytest's
    monkeypatch only undoes mutations made via `monkeypatch.setenv` /
    `delenv`, not direct os.environ writes, so a settings PATCH test
    that leaves the process with `INTERNAL_LLM_PROVIDER=quenchforge`
    will poison every downstream test that reads that env var.

    Snapshot the relevant env vars at fixture setup and restore at
    teardown so the file is well-behaved when interleaved with the
    rest of the suite.
    """
    import config

    monkeypatch.setattr(config, "SYNC_DIR", "")
    # Start with known values so each test asserts a state change.
    monkeypatch.setattr(config, "INTERNAL_LLM_PROVIDER", "openrouter", raising=False)
    monkeypatch.setattr(config, "INTERNAL_LLM_MODEL", "", raising=False)
    _PROVIDER_ENV_VARS = (
        "INTERNAL_LLM_PROVIDER",
        "INTERNAL_LLM_MODEL",
    )
    saved = {k: os.environ.get(k) for k in _PROVIDER_ENV_VARS}
    for k in _PROVIDER_ENV_VARS:
        os.environ.pop(k, None)

    app = FastAPI()
    app.include_router(router)
    yield TestClient(app)

    # Restore any pre-existing values the PATCH handler may have
    # clobbered. None ↔ unset round-trip preserves both states.
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_get_settings_includes_internal_llm_fields(client):
    """The GET response must surface the current internal LLM routing."""
    r = client.get("/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["internal_llm_provider"] == "openrouter"
    # Empty config + empty env → falls back to OLLAMA_DEFAULT_MODEL.
    assert body["internal_llm_model"]  # non-empty string


def test_patch_internal_llm_provider_to_quenchforge(client):
    """Flipping the provider mutates BOTH config attr and os.environ."""
    import config

    r = client.patch("/settings", json={"internal_llm_provider": "quenchforge"})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == {"internal_llm_provider": "quenchforge"}
    # Both surfaces must reflect the change for live-process effect.
    assert config.INTERNAL_LLM_PROVIDER == "quenchforge"
    assert os.environ.get("INTERNAL_LLM_PROVIDER") == "quenchforge"


def test_patch_internal_llm_provider_to_ollama(client):
    import config

    r = client.patch("/settings", json={"internal_llm_provider": "ollama"})
    assert r.status_code == 200
    assert config.INTERNAL_LLM_PROVIDER == "ollama"
    assert os.environ.get("INTERNAL_LLM_PROVIDER") == "ollama"


def test_patch_internal_llm_provider_rejects_invalid(client):
    """Unknown providers must 400 — silent acceptance would silently break
    every downstream call_internal_llm caller until container restart."""
    r = client.patch("/settings", json={"internal_llm_provider": "openai"})
    assert r.status_code == 400
    assert "internal_llm_provider" in r.text


def test_patch_internal_llm_model(client):
    """Model string mutates both surfaces; no validation on value (any
    string the chosen provider accepts is valid)."""
    import config

    r = client.patch("/settings", json={"internal_llm_model": "llama3.1-8b"})
    assert r.status_code == 200
    assert config.INTERNAL_LLM_MODEL == "llama3.1-8b"
    assert os.environ.get("INTERNAL_LLM_MODEL") == "llama3.1-8b"


def test_patch_provider_and_model_together(client):
    """The typical operator flow — flip both fields in one request."""
    import config

    r = client.patch("/settings", json={
        "internal_llm_provider": "quenchforge",
        "internal_llm_model": "qwen2.5:7b-instruct-q4_k_m",
    })
    assert r.status_code == 200
    upd = r.json()["updated"]
    assert upd["internal_llm_provider"] == "quenchforge"
    assert upd["internal_llm_model"] == "qwen2.5:7b-instruct-q4_k_m"
    assert config.INTERNAL_LLM_PROVIDER == "quenchforge"
    assert config.INTERNAL_LLM_MODEL == "qwen2.5:7b-instruct-q4_k_m"


def test_patch_no_changes_returns_400(client):
    """An empty PATCH must return 400 rather than silently no-op."""
    r = client.patch("/settings", json={})
    assert r.status_code == 400
