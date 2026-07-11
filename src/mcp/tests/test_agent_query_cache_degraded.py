# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Budget-degraded envelopes must never enter the query cache.

Eval finding (2026-07-09, V1 Phase 4): under CPU saturation ``/agent/query``
returns HTTP-200 *degraded* envelopes (``budget_exceeded: true``, kb/memory
``timeout``, external-fallback-only sources). The router cached them like any
other result, so one saturated query kept serving fallback-only hits after
the load transient passed. This file pins the guard: degraded → no cache
write; healthy → cached as before.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """FastAPI TestClient with stubbed external deps."""
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


def _envelope(**extra) -> dict:
    base = {
        "context": "",
        "sources": [],
        "confidence": 0.0,
        "domains_searched": [],
        "total_results": 0,
        "results": [],
    }
    base.update(extra)
    return base


class TestDegradedEnvelopeCaching:
    @pytest.mark.asyncio
    async def test_degraded_envelope_not_cached(self, client):
        """``budget_exceeded: true`` results skip the cache write."""
        with (
            patch(
                "core.agents.query_agent.agent_query",
                new=AsyncMock(return_value=_envelope(budget_exceeded=True)),
            ),
            patch("utils.query_cache.get_cached", return_value=None),
            patch("utils.query_cache.set_cached") as mock_set,
        ):
            res = client.post(
                "/agent/query",
                json={"query": "anything", "use_reranking": False},
            )
        assert res.status_code == 200, res.text
        mock_set.assert_not_called()

    @pytest.mark.asyncio
    async def test_healthy_envelope_still_cached(self, client):
        """The guard must not disable caching for normal results."""
        with (
            patch(
                "core.agents.query_agent.agent_query",
                new=AsyncMock(return_value=_envelope()),
            ),
            patch("utils.query_cache.get_cached", return_value=None),
            patch("utils.query_cache.set_cached") as mock_set,
        ):
            res = client.post(
                "/agent/query",
                json={"query": "anything", "use_reranking": False},
            )
        assert res.status_code == 200, res.text
        mock_set.assert_called_once()
