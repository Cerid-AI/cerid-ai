# Copyright (c) 2026 Justin Michaels. All rights reserved.
"""Envelope invariant at the /agent/query router edge (preservation I2).

Every response carries ``source_breakdown``. The orchestrated (smart)
path builds the full kb/memory/external split; the manual path only
inherited one when low confidence detoured through CRAG enrichment —
high-confidence answers on a rich corpus returned a slim envelope
(2026-07-12 master-instance preservation run; CI's near-empty corpus
always took the enriched path, hiding the gap).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import agents


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(agents.router)

    with (
        patch.object(agents, "get_chroma", return_value=MagicMock()),
        patch.object(agents, "get_neo4j", return_value=MagicMock()),
        patch.object(agents, "get_graph_store", return_value=MagicMock()),
        patch.object(agents, "get_redis", return_value=MagicMock()),
    ):
        yield TestClient(app, raise_server_exceptions=False)


def _high_confidence_result() -> dict:
    src = {"content": "spark docs", "relevance": 0.97, "source_type": "kb"}
    return {
        "context": "ctx",
        "sources": [src, dict(src)],
        "results": [dict(src), dict(src)],
        "confidence": 1.0,
        "domains_searched": ["general"],
        "total_results": 2,
    }


def test_manual_mode_always_carries_source_breakdown(client):
    with patch(
        "core.agents.query_agent.agent_query_full",
        new=AsyncMock(return_value=_high_confidence_result()),
    ):
        res = client.post(
            "/agent/query",
            json={"query": "what is parallel computing", "skip_cache": True},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    bd = body.get("source_breakdown")
    assert bd is not None, "manual-mode envelope missing source_breakdown"
    assert set(bd) == {"kb", "memory", "external"}
    assert sum(len(v) for v in bd.values()) == len(body["results"])
    assert body.get("strategy"), "manual-mode envelope missing strategy"


def test_existing_breakdown_is_not_overwritten(client):
    enriched = _high_confidence_result()
    enriched["source_breakdown"] = {
        "kb": enriched["sources"][:1],
        "memory": [],
        "external": [enriched["sources"][1]],
    }
    with patch(
        "core.agents.query_agent.agent_query_full",
        new=AsyncMock(return_value=enriched),
    ):
        res = client.post(
            "/agent/query",
            json={"query": "what is parallel computing", "skip_cache": True},
        )
    assert res.status_code == 200, res.text
    assert len(res.json()["source_breakdown"]["external"]) == 1
