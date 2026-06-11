# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression: ``/agent/query`` must bind ``low_confidence`` on the smart path.

Eval finding (2026-06-11): the smart / custom_smart branch called
``orchestrated_query`` and then fell straight through to
``result["low_confidence"] = _kb_low_conf`` — but ``_kb_low_conf`` was only
assigned inside the *manual* branch. Every ``rag_mode="smart"`` request (the
chat UI default — ``settings.rag_mode ?? "smart"``) raised
``UnboundLocalError`` and returned HTTP 500, breaking RAG retrieval for the
default configuration.

These tests pin the contract end-to-end through the router so a future refactor
that re-introduces the unbound-variable path is caught at PR time.
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
            "source_status": {"kb": "ok"},
            "source_breakdown": {"kb": results, "memory": [], "external": []},
        }

    return AsyncMock(side_effect=_impl)


@pytest.mark.parametrize("rag_mode", ["smart", "custom_smart"])
def test_smart_mode_does_not_crash_and_binds_low_confidence(client, rag_mode):
    """smart / custom_smart return 200 with ``low_confidence`` present."""
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
    # The bug returned {"detail": "cannot access local variable '_kb_low_conf'..."}.
    assert "low_confidence" in body, f"low_confidence missing for {rag_mode}: {body}"
    # Strong KB hit → not low-confidence.
    assert body["low_confidence"] is False


def test_smart_mode_weak_kb_marks_low_confidence(client):
    """A weak/empty orchestrated result still binds low_confidence = True."""
    with patch(
        "app.agents.retrieval_orchestrator.orchestrated_query",
        new=_fake_orchestrated([]),
    ):
        res = client.post(
            "/agent/query",
            json={"query": "obscure", "rag_mode": "smart", "skip_cache": True},
        )

    assert res.status_code == 200, res.text
    assert res.json()["low_confidence"] is True
