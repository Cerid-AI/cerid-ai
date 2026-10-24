# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Graph expansion adds no row from a domain the caller may not read (audit F87-F91).

Graph expansion follows relationship and entity edges from the scanned rows
to related artifacts, wherever they live. Nothing filtered what it brought
back, so a strict coding query, or a walled consumer, received related chunks
from finance or iMessage. The expansion is faked to return such a row; the
scope filter and the /agent/query handler are real.
"""
from __future__ import annotations

from typing import Any

import pytest

from tests.test_named_domains_are_a_hard_scope import (  # noqa: F401 — chroma is a fixture
    KB_ONLY,
    _ask,
    chroma,
)

pytestmark = pytest.mark.usefixtures("chroma")


def _related(domain: str) -> dict[str, Any]:
    return {
        "content": f"a related chunk filed under {domain}", "relevance": 0.99, "domain": domain,
        "artifact_id": f"related-{domain}", "chunk_id": f"related-{domain}-0", "filename": f"{domain}.md",
        "graph_source": True,
    }


@pytest.fixture
def graph_brings(monkeypatch):
    def install(domain: str) -> None:
        async def expand(results, **_k):
            return [*results, _related(domain)]

        monkeypatch.setattr("core.agents.query_agent.graph_expand_results", expand)
        monkeypatch.setattr("core.agents.query_agent._should_skip_graph", lambda *a, **k: False)
        monkeypatch.setattr("config.features.RETRIEVAL_MODE", "baseline", raising=False)

    return install


def _graph_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in result["results"] if r.get("graph_source")]


@pytest.mark.asyncio
async def test_a_strict_query_gets_no_related_row_from_another_domain(graph_brings):
    graph_brings("finance")

    result = await _ask(domains=["coding"], strict_domains=True, context_sources=KB_ONLY)

    assert _graph_rows(result) == []
    assert {r["domain"] for r in result["results"]} <= {"coding"}


@pytest.mark.asyncio
async def test_a_walled_consumer_gets_no_related_row_outside_its_wall(graph_brings):
    graph_brings("coding")

    result = await _ask("cerid-finance", domains=["finance"], context_sources=KB_ONLY)

    assert _graph_rows(result) == []


@pytest.mark.asyncio
async def test_an_unscoped_query_gets_no_related_row_from_a_hidden_domain(graph_brings, monkeypatch):
    monkeypatch.setattr("core.agents.query_agent.sensitive_domains_opted_in", lambda: False)
    graph_brings("imessage")

    result = await _ask(context_sources=KB_ONLY)

    assert _graph_rows(result) == []


@pytest.mark.asyncio
async def test_an_unscoped_query_keeps_an_ordinary_related_row(graph_brings):
    graph_brings("finance")

    result = await _ask(context_sources=KB_ONLY)

    assert [r["domain"] for r in _graph_rows(result)] == ["finance"]
