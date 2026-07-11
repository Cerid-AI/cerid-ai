# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-request retrieval budget override (V1 Phase 4, 2026-07-10).

The interactive default (``AGENT_QUERY_BUDGET_SECONDS`` = 20s) is right for
chat UX but starves offline/batch callers on CPU-bound deployments: the eval
tier's benchmark queries measured ~19s at rest whenever the background
processor shared the local inference backend, making the retrieval-quality
gate unmeasurable under ambient load. Explicit callers (the eval harness,
SDK batch jobs) opt into patience per request; the interactive default is
untouched.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_budget_override_times_out_to_degraded():
    from core.agents.query_agent import agent_query

    async def slow_impl(**_kw):
        await asyncio.sleep(0.5)
        return {"results": []}

    with patch("core.agents.query_agent._agent_query_impl", new=slow_impl):
        out = await agent_query(query="q", budget_seconds=0.05)

    assert out.get("budget_exceeded") is True
    assert out.get("budget_seconds") == 0.05


@pytest.mark.asyncio
async def test_budget_override_allows_completion():
    from core.agents.query_agent import agent_query

    async def fast_impl(**_kw):
        return {"results": [], "sources": [], "marker": "ok"}

    with patch("core.agents.query_agent._agent_query_impl", new=fast_impl):
        out = await agent_query(query="q", budget_seconds=5.0)

    assert out.get("marker") == "ok"


@pytest.mark.asyncio
async def test_default_budget_used_when_not_overridden():
    """No override → the configured default reaches wait_for unchanged."""
    import config
    from core.agents.query_agent import agent_query

    async def fast_impl(**_kw):
        return {"results": [], "marker": "ok"}

    with patch("core.agents.query_agent._agent_query_impl", new=fast_impl), \
         patch.object(config, "AGENT_QUERY_BUDGET_SECONDS", 0.05):

        async def slow_impl(**_kw):
            await asyncio.sleep(0.5)
            return {"results": []}

        with patch("core.agents.query_agent._agent_query_impl", new=slow_impl):
            out = await agent_query(query="q")

    assert out.get("budget_exceeded") is True
    assert out.get("budget_seconds") == 0.05


@pytest.fixture
def client():
    from app.routers import agents

    app = FastAPI()
    app.include_router(agents.router)

    with (
        patch.object(agents, "get_chroma", return_value=MagicMock()),
        patch.object(agents, "get_neo4j", return_value=MagicMock()),
        patch.object(agents, "get_graph_store", return_value=MagicMock()),
        patch.object(agents, "get_redis", return_value=MagicMock()),
    ):
        yield TestClient(app, raise_server_exceptions=False)


class TestBudgetOverrideAPI:
    @pytest.mark.asyncio
    async def test_budget_seconds_forwarded(self, client):
        captured: dict = {}

        async def fake_full(**kwargs):
            captured.update(kwargs)
            return {
                "context": "", "sources": [], "confidence": 0.0,
                "domains_searched": [], "total_results": 0, "results": [],
            }

        with patch(
            "core.agents.query_agent.agent_query_full",
            new=AsyncMock(side_effect=fake_full),
        ):
            res = client.post(
                "/agent/query",
                json={"query": "q", "budget_seconds": 60, "skip_cache": True,
                      "use_reranking": False},
            )
        assert res.status_code == 200, res.text
        assert captured.get("budget_seconds") == 60

    def test_budget_seconds_schema_bounds(self, client):
        """Boundary validation at the API edge: 1 <= budget_seconds <= 120."""
        for bad in (0, 0.5, 121, -3):
            res = client.post(
                "/agent/query",
                json={"query": "q", "budget_seconds": bad},
            )
            assert res.status_code == 422, f"budget_seconds={bad} not rejected"
