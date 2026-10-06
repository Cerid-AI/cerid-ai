# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""/query answers 403 when the consumer's allow-list removes every requested domain.

It answered an empty 200, which reads as "nothing indexed": the nightly
retrieval eval sent an unregistered client ID and reported 0 of 18 fixtures
retrievable for ten days before anyone saw it was a scope wall. /sdk/v1/query
already answered 403; the suite contract says a restricted query errors.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# The shape query_agent.agent_query_full returns when the filter empties the
# domain list (core/agents/query_agent.py, "Consumer domain isolation").
RESTRICTED = {
    "context": "", "sources": [], "confidence": 0.0, "domains_searched": [],
    "total_results": 0, "results": [],
    "retrieval_skipped": True, "retrieval_reason": "consumer_domain_restricted",
}


@pytest.fixture()
def client(monkeypatch):
    from app.routers import query as query_router

    monkeypatch.setattr(query_router, "private_blocks", lambda level: False)
    for dep in ("get_chroma", "get_neo4j", "get_redis", "get_graph_store"):
        monkeypatch.setattr(query_router, dep, lambda: object())
    app = FastAPI()
    app.include_router(query_router.router)
    return TestClient(app)


def test_a_fully_restricted_query_is_403(client):
    with patch("core.agents.query_agent.agent_query_full", new_callable=AsyncMock, return_value=RESTRICTED):
        res = client.post("/query", json={"query": "q", "domain": "coding"}, headers={"X-Client-ID": "cerid-finance"})
    assert res.status_code == 403
    assert res.json()["detail"]["retrieval_reason"] == "consumer_domain_restricted"


def test_the_consumer_scope_reaches_retrieval(client):
    with patch("core.agents.query_agent.agent_query_full", new_callable=AsyncMock, return_value=RESTRICTED) as spy:
        client.post("/query", json={"query": "q", "domain": "coding"}, headers={"X-Client-ID": "cerid-finance"})
    assert spy.await_args.kwargs["allowed_domains"] == ["finance", "inbox"]
    assert spy.await_args.kwargs["domain_record_types"] == {"inbox": ["mail_financial_card"]}


def test_an_empty_but_permitted_query_is_still_200(client):
    empty = {"context": "", "sources": [], "confidence": 0.0}
    with patch("core.agents.query_agent.agent_query_full", new_callable=AsyncMock, return_value=empty):
        res = client.post("/query", json={"query": "q", "domain": "finance"}, headers={"X-Client-ID": "cerid-finance"})
    assert res.status_code == 200
    assert res.json()["sources"] == []
