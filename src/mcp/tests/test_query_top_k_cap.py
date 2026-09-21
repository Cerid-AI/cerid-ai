# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""``top_k`` is a hard cap on what a query returns (F346).

``SDKSearchRequest.top_k`` is documented as "Maximum results to return"
(1..100, default 3) and nothing enforced it: retrieval fans out per domain
at ``top_k`` each and the union was never truncated, so a default
``/sdk/v1/query`` returned 63 results in a 410 KB body and ``/sdk/v1/search``
returned 4 for ``top_k=1``.

The cap belongs in ``agent_query_full`` — the single entry REST, MCP and A2A
all route through — not in each router.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def _oversized_envelope(n: int = 63) -> dict:
    rows = [
        {"id": f"c{i}", "content": f"chunk {i}", "relevance": 1.0 - i / 100, "domain": "general"}
        for i in range(n)
    ]
    return {
        "context": "".join(r["content"] for r in rows),
        "sources": list(rows),
        "confidence": 0.5,
        "domains_searched": ["general"],
        "total_results": n,
        "token_budget_used": 43292,
        "graph_results": 0,
        "results": list(rows),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("top_k", [1, 3, 10])
async def test_agent_query_full_caps_results_and_sources(top_k):
    from core.agents import query_agent

    with patch.object(
        query_agent, "agent_query", AsyncMock(return_value=_oversized_envelope())
    ):
        result = await query_agent.agent_query_full(
            "python xml parser",
            top_k=top_k,
            external_augmentation=False,
        )

    assert len(result["results"]) == top_k, len(result["results"])
    assert len(result["sources"]) == top_k, len(result["sources"])
    assert result["total_results"] == top_k


@pytest.mark.asyncio
async def test_a_short_result_set_is_left_alone():
    from core.agents import query_agent

    with patch.object(
        query_agent, "agent_query", AsyncMock(return_value=_oversized_envelope(2))
    ):
        result = await query_agent.agent_query_full(
            "q", top_k=10, external_augmentation=False
        )

    assert len(result["results"]) == 2
    assert result["total_results"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("top_k", [1, 3, 10])
async def test_sdk_search_returns_no_more_than_top_k(top_k):
    """The published contract, exercised through the router."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import sdk
    from core.agents import query_agent

    app = FastAPI()
    app.include_router(sdk.router)

    with patch.object(
        query_agent, "agent_query", AsyncMock(return_value=_oversized_envelope())
    ), patch("app.routers.sdk.private_blocks", return_value=False), patch(
        "app.deps.get_chroma"
    ), patch("app.deps.get_redis"), patch("app.deps.get_neo4j"), patch(
        "app.deps.get_graph_store"
    ):
        resp = TestClient(app).post(
            "/sdk/v1/search", json={"query": "python xml parser", "top_k": top_k}
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_results"] == top_k
    assert len(body["results"]) == top_k
