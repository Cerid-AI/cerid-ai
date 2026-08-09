# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Regression: ``/agent/query`` smart path must not 500.

Eval finding (2026-06-11): the smart / custom_smart branch called
``orchestrated_query`` and then fell straight through to
``result["low_confidence"] = _kb_low_conf`` — but ``_kb_low_conf`` was only
assigned inside the *manual* branch. Every ``rag_mode="smart"`` request raised
``UnboundLocalError`` and returned HTTP 500.

E1 CR-032 removed the write-only ``low_confidence`` stamp entirely. These tests
still pin that smart mode returns 200 without the UnboundLocalError path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """FastAPI TestClient with stubbed external deps (mirrors pack-scope test)."""
    from app.routers import agents

    app = FastAPI()
    app.include_router(agents.router)

    with (
        patch.object(agents, "get_chroma", return_value=MagicMock()),
        patch.object(agents, "get_neo4j", return_value=MagicMock()),
        patch.object(agents, "get_graph_store", return_value=MagicMock()),
        patch.object(agents, "get_redis", return_value=MagicMock()),
    ):
        # raise_server_exceptions=False so a 500 surfaces as a response, not an
        # exception — that is exactly the failure mode this regression guards.
        yield TestClient(app, raise_server_exceptions=False)


def _fake_orchestrated(results):
    async def _impl(**kwargs):
        return {
            "context": "ctx",
            "sources": results,
            "confidence": (results[0]["relevance"] if results else 0.0),
            "domains_searched": ["general"],
            "total_results": len(results),
            "results": results,
            "source_breakdown": {"kb": results, "memory": [], "external": []},
        }

    return AsyncMock(side_effect=_impl)


@pytest.mark.parametrize("rag_mode", ["smart", "custom_smart"])
def test_smart_mode_returns_200_without_unbound_local(client, rag_mode):
    """smart / custom_smart return 200 (the UnboundLocalError path is gone)."""
    strong = [{"relevance": 0.85, "content": "strong hit"}]
    with patch(
        "app.agents.retrieval_orchestrator.orchestrated_query",
        new=_fake_orchestrated(strong),
    ):
        res = client.post(
            "/agent/query",
            json={"query": "What is Cerid AI?", "rag_mode": rag_mode, "skip_cache": True},
        )

    assert res.status_code == 200, res.text
    body = res.json()
    # Must not be the UnboundLocalError 500 payload.
    assert "detail" not in body or "UnboundLocal" not in str(body.get("detail", ""))
    # E1 CR-032: write-only stamp removed.
    assert "low_confidence" not in body


def test_smart_mode_weak_kb_still_200(client):
    """A weak/empty orchestrated result returns 200 without low_confidence stamp."""
    with patch(
        "app.agents.retrieval_orchestrator.orchestrated_query",
        new=_fake_orchestrated([]),
    ):
        res = client.post(
            "/agent/query",
            json={"query": "obscure", "rag_mode": "smart", "skip_cache": True},
        )

    assert res.status_code == 200, res.text
    assert "low_confidence" not in res.json()
