# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A consumer's record-typed read of a domain it does not own.

Triage files the financial card in `inbox`. cerid-finance needs that row and
nothing else from the mail domain, so its registry entry carries
``record_types: {"inbox": ["mail_financial_card"]}``. The context carries it,
every transport hands it to retrieval, and the retrieval chokepoint fuses it
into the `where` of that one domain — vector and BM25 — leaving the other
domains' clauses alone.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
from app.services.request_policy import build_request_context
from core.agents import query_agent

CARD = {"record_type": "mail_financial_card", "source": "inbox_triage"}
THREAD = {"record_type": "mail_thread", "source": "inbox_triage"}


def _matches(meta: dict[str, Any], where: dict[str, Any] | None) -> bool:
    """A small Chroma: equality, $in, $and."""
    if not where:
        return True
    if "$and" in where:
        return all(_matches(meta, clause) for clause in where["$and"])
    for key, expected in where.items():
        if isinstance(expected, dict) and "$in" in expected:
            if meta.get(key) not in expected["$in"]:
                return False
        elif meta.get(key) != expected:
            return False
    return True


class _Chroma:
    """Two domains: finance holds a ledger note; inbox holds a card and a thread."""

    def __init__(self) -> None:
        self.where_by_domain: dict[str, Any] = {}
        self.rows = {
            config.collection_name("finance"): [("ledger", "October ledger", {"source": "cerid-finance"})],
            config.collection_name("inbox"): [
                ("card", "Payee: City Power\nAmount: 42.10", CARD),
                ("thread", "Patio work account 999", THREAD),
            ],
        }

    def list_collections(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=n) for n in self.rows]

    def get_collection(self, name: str) -> Any:
        rows = self.rows[name]

        def _query(**kw: Any) -> dict[str, Any]:
            self.where_by_domain[name] = kw.get("where")
            kept = [r for r in rows if _matches(r[2], kw.get("where"))]
            return {
                "ids": [[r[0] for r in kept]],
                "documents": [[r[1] for r in kept]],
                "metadatas": [[r[2] for r in kept]],
                "distances": [[0.1 for _ in kept]],
            }

        return SimpleNamespace(query=_query, count=lambda: len(rows))


@pytest.fixture
def _plain_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.retrieval import bm25 as bm25_mod

    monkeypatch.setenv("CERID_FILTER_PENDING_CHUNKS", "false")
    monkeypatch.setattr(bm25_mod, "is_available", lambda: False)
    monkeypatch.setattr(query_agent, "_parent_child_enabled", lambda: False)
    monkeypatch.setattr(query_agent, "_unsearchable_folder_ids", lambda: set())


def test_the_context_carries_the_consumers_record_types():
    ctx = build_request_context(client_id="cerid-finance", private_level=0)
    assert ctx.allowed_domains_list() == ["finance", "inbox"]
    assert ctx.domain_record_types_dict() == {"inbox": ["mail_financial_card"]}
    assert build_request_context(client_id="gui", private_level=0).domain_record_types_dict() is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("_plain_retrieval")
async def test_a_cerid_finance_query_returns_the_card_and_not_a_mail_thread():
    chroma = _Chroma()
    results = await query_agent.multi_domain_query(
        query="power bill", domains=["finance", "inbox"], top_k=5, chroma_client=chroma,
        domain_record_types={"inbox": ["mail_financial_card"]},
    )
    ids = sorted(r["chunk_id"] for r in results)
    assert ids == ["card", "ledger"]
    assert chroma.where_by_domain[config.collection_name("inbox")] == {"record_type": "mail_financial_card"}
    assert chroma.where_by_domain[config.collection_name("finance")] is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("_plain_retrieval")
async def test_the_record_type_clause_is_fused_with_a_callers_filter():
    chroma = _Chroma()
    results = await query_agent.multi_domain_query(
        query="power bill", domains=["inbox"], top_k=5, chroma_client=chroma,
        metadata_filter={"source": "inbox_triage"},
        domain_record_types={"inbox": ["mail_financial_card", "mail_finance_pointer"]},
    )
    assert [r["chunk_id"] for r in results] == ["card"]
    assert chroma.where_by_domain[config.collection_name("inbox")] == {"$and": [
        {"source": "inbox_triage"},
        {"record_type": {"$in": ["mail_financial_card", "mail_finance_pointer"]}},
    ]}


@pytest.mark.asyncio
@pytest.mark.usefixtures("_plain_retrieval")
async def test_without_a_grant_the_domain_is_unfiltered():
    chroma = _Chroma()
    results = await query_agent.multi_domain_query(
        query="power bill", domains=["inbox"], top_k=5, chroma_client=chroma,
    )
    assert sorted(r["chunk_id"] for r in results) == ["card", "thread"]


def test_the_bm25_side_applies_the_same_clause():
    """BM25-only hits bypass Chroma's where; the same clause is checked in Python."""
    assert query_agent._metadata_matches(CARD, {"record_type": "mail_financial_card"})
    assert not query_agent._metadata_matches(THREAD, {"record_type": "mail_financial_card"})
    fused = {"$and": [{"source": "inbox_triage"}, {"record_type": {"$in": ["mail_financial_card"]}}]}
    assert query_agent._metadata_matches(CARD, fused)
    assert not query_agent._metadata_matches(THREAD, fused)
    assert query_agent._metadata_matches(THREAD, None)


@pytest.fixture
def sdk_client():
    from app.routers.sdk import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_the_sdk_search_route_hands_the_grant_to_retrieval(sdk_client, monkeypatch):
    """/sdk/v1/search calls agent_query_full itself. /sdk/v1/query goes through
    the /agent/query handler, covered in test_named_domains_are_a_hard_scope;
    /query in test_query_restricted_is_403."""
    monkeypatch.setattr("app.services.private_mode.get_private_mode_level", lambda: 0)
    spy = AsyncMock(return_value={"results": [], "total_results": 0, "confidence": 0.0})
    with (
        patch("core.agents.query_agent.agent_query_full", spy),
        patch("app.deps.get_chroma", lambda: MagicMock()),
        patch("app.deps.get_redis", lambda: MagicMock()),
        patch("app.deps.get_neo4j", lambda: MagicMock()),
        patch("app.deps.get_graph_store", lambda: MagicMock()),
    ):
        resp = sdk_client.post(
            "/sdk/v1/search", json={"query": "power bill", "domain": "inbox"},
            headers={"X-Client-ID": "cerid-finance"},
        )
    assert resp.status_code == 200, resp.text
    assert spy.call_args.kwargs["allowed_domains"] == ["finance", "inbox"]
    assert spy.call_args.kwargs["domain_record_types"] == {"inbox": ["mail_financial_card"]}
