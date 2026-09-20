# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""F072 — the proxy's error text must name the backend it actually calls.

``_ollama_base_url()`` switches to ``QUENCHFORGE_URL`` when the provider is
quenchforge, but every failure path told the operator to run ``ollama serve``
— which would bind the port Quenchforge needs, obstructing the recovery it
was supposed to guide.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def quenchforge_client(monkeypatch):
    from app.routers.ollama_proxy import router

    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")
    monkeypatch.setenv("QUENCHFORGE_URL", "http://127.0.0.1:11434")
    monkeypatch.delenv("OLLAMA_ENABLED", raising=False)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _detail_on_connect_error(client, method: str, path: str, **kw) -> str:
    with patch(
        "httpx.AsyncClient.get",
        AsyncMock(side_effect=httpx.ConnectError("refused")),
    ), patch(
        "httpx.AsyncClient.post",
        AsyncMock(side_effect=httpx.ConnectError("refused")),
    ):
        resp = getattr(client, method)(path, **kw)
    return resp.json()["detail"]


class TestQuenchforgeRemediation:
    def test_model_listing_does_not_tell_the_operator_to_run_ollama_serve(
        self, quenchforge_client
    ):
        detail = _detail_on_connect_error(quenchforge_client, "get", "/ollama/models")

        assert "try: ollama serve" not in detail.lower(), (
            "the proxy told a quenchforge operator to start the daemon that "
            "would steal the port"
        )
        assert "quenchforge" in detail.lower()
        assert "do not run `ollama serve`" in detail.lower()

    def test_chat_failure_names_quenchforge(self, quenchforge_client):
        detail = _detail_on_connect_error(
            quenchforge_client,
            "post",
            "/ollama/chat",
            json={"model": "llama3.1-8b", "messages": [{"role": "user", "content": "hi"}]},
        )

        assert "try: ollama serve" not in detail.lower()
        assert "quenchforge" in detail.lower()

    def test_disabled_message_names_both_switches(self, monkeypatch):
        from app.routers.ollama_proxy import router

        monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "openrouter")
        monkeypatch.delenv("OLLAMA_ENABLED", raising=False)
        app = FastAPI()
        app.include_router(router)
        resp = TestClient(app).get("/ollama/models")

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert "INTERNAL_LLM_PROVIDER=quenchforge" in detail, (
            "the 503 named only OLLAMA_ENABLED, though the quenchforge provider "
            "also enables this router"
        )


class TestOllamaRemediationUnchanged:
    def test_an_ollama_deployment_still_gets_the_ollama_hint(self, monkeypatch):
        from app.routers.ollama_proxy import router

        monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "ollama")
        monkeypatch.setenv("OLLAMA_ENABLED", "true")
        monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:11434")
        app = FastAPI()
        app.include_router(router)

        detail = _detail_on_connect_error(TestClient(app), "get", "/ollama/models")

        assert "try: ollama serve" in detail.lower()
