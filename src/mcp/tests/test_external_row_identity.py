# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Every external row carries a stable identity to the response (audit F26).

A row's only handle was ``source_url``: a custom API stamped its base URL on
every row, and DuckDuckGo's abstract can arrive with none, so rows collided or
had nothing to be deduplicated or cited by. ``DataSourceResult.guid`` is the
identity; CRAG carries it onto the merged row. Sources, registry and the CRAG
merge are real; the network and the cross-encoder are faked.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.data_sources.base import DataSource, DataSourceRegistry, DataSourceResult
from app.data_sources.custom import CustomApiSource
from core.agents import crag

_ROWS = {"data": [
    {"title": "Q3 revenue", "content": "Revenue rose 4% in the third quarter."},
    {"title": "Q3 margin", "content": "Gross margin held at 41%."},
]}


@pytest.fixture
def custom_api(monkeypatch):
    real_client = httpx.AsyncClient

    def patched_client(**kwargs):
        transport = httpx.MockTransport(lambda _req: httpx.Response(200, json=_ROWS))
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)
    monkeypatch.setattr("app.data_sources.custom.guard_or_log", lambda *_a, **_k: True)


def _source() -> CustomApiSource:
    return CustomApiSource(source_id="acme", display_name="Acme Search", base_url="https://api.example.com")


@pytest.mark.asyncio
async def test_custom_rows_sharing_the_base_url_are_distinct(custom_api):
    rows = [r.to_dict() for r in await _source().query("last quarter")]
    again = [r.to_dict() for r in await _source().query("last quarter")]

    assert {r["source_url"] for r in rows} == {"https://api.example.com"}
    assert all(r["guid"] for r in rows)
    assert len({r["guid"] for r in rows}) == len(rows)
    assert [r["guid"] for r in rows] == [r["guid"] for r in again]


class _Fixed(DataSource):
    name = "fixed"

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        return [
            DataSourceResult(title="A", content="first", source_name="Fixed"),
            DataSourceResult(title="B", content="second", source_name="Fixed"),
        ]


@pytest.mark.asyncio
async def test_crag_carries_the_identity_onto_the_merged_row():
    registry = DataSourceRegistry()
    registry.register(_Fixed())

    async def accept_all(_query, rows):
        return rows

    with (
        patch.object(crag, "_external_registry", registry),
        patch.object(crag, "_search_term_extractor", lambda q: q),
        patch.object(crag, "judge_external", accept_all),
        patch("core.routing.egress.cloud_egress_refused", return_value=""),
    ):
        result = await crag.augment_external_crag({"results": []}, "a question", None, 0.6)

    expected = sorted(r.guid for r in await _Fixed().query(""))
    merged = [r for r in result["results"] if r.get("source_type") == "external"]
    assert sorted(r.get("guid", "") for r in merged) == expected
    assert sorted(r.get("guid", "") for r in result["source_breakdown"]["external"]) == expected


@pytest.mark.asyncio
async def test_smart_mode_carries_the_identity_onto_the_merged_row():
    from app.agents.retrieval_orchestrator import orchestrated_query

    fetched = [r.to_dict() for r in await _Fixed().query("")]
    kb = {"results": [], "sources": [], "context": "", "confidence": 0.0, "total_results": 0}

    async def accept_all(_query, rows):
        return rows

    with (
        patch("app.agents.retrieval_orchestrator._query_external_sources", new=AsyncMock(return_value=fetched)),
        patch("app.agents.retrieval_orchestrator.judge_external", accept_all),
        patch("core.agents.query_agent.agent_query", new=AsyncMock(return_value=kb)),
    ):
        result = await orchestrated_query(
            "a question", rag_mode="smart", context_sources={"kb": True, "memory": False, "external": True},
        )

    assert sorted(r.get("guid", "") for r in result["source_breakdown"]["external"]) == sorted(
        r["guid"] for r in fetched
    )
