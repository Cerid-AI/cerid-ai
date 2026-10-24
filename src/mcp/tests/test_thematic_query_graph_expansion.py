# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A thematic query under RETRIEVAL_MODE=auto gets graph expansion (audit F129).

Auto mode used to send long, quote-free, proper-noun-free questions to a
community-summary expansion. Its rows (domain ``graph``) raised the result
count, so the entity and baseline fallbacks never ran, and then the
readable-domain filter dropped every one of them: no graph expansion reached
the answer while ``graph_results`` reported 1. The summary path is retired;
the entity expansion runs, and ``graph_results`` counts only graph rows that
reach the results. Chroma and Neo4j are faked; /agent/query runs for real.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import config
from app.routers import agents
from tests.test_named_domains_are_a_hard_scope import (  # noqa: F401 — chroma is a fixture
    KB_ONLY,
    _ask,
    chroma,
)

THEMATIC = (
    "how do the broad themes across my notes on testing and refactoring "
    "relate to one another and change over the long run"
)
SEED, RELATED = "seed-artifact", "related-artifact"


def _artifact_in(where: Any) -> str | None:
    if isinstance(where, dict):
        for key, value in where.items():
            if key == "artifact_id":
                return value.get("$eq") if isinstance(value, dict) else value
            found = _artifact_in(value)
            if found:
                return found
    if isinstance(where, list):
        for item in where:
            found = _artifact_in(item)
            if found:
                return found
    return None


class _Chroma:
    """``coding`` holds the seed chunk; an artifact-scoped fetch returns that artifact's chunk."""

    def list_collections(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=config.collection_name(d)) for d in config.DOMAINS]

    def get_collection(self, name: str) -> Any:
        def query(where: Any = None, **_kw: Any) -> dict[str, Any]:
            artifact = _artifact_in(where) or (SEED if name == config.collection_name("coding") else None)
            if artifact is None:
                return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
            return {
                "ids": [[f"{artifact}-chunk-0"]],
                "documents": [[f"notes on testing and refactoring themes ({artifact})"]],
                "metadatas": [[{"artifact_id": artifact, "filename": f"{artifact}.md", "chunk_index": 0}]],
                "distances": [[0.3]],
            }

        return SimpleNamespace(query=query, count=lambda: 1)


class _Neo4j:
    """The seed shares entities with one related artifact, filed under ``related_domain``.

    A community over those entities carries a summary too, so the retired
    summary path, were it still wired, would find it.
    """

    def __init__(self, related_domain: str) -> None:
        self.related_domain = related_domain

    def execute_query(self, cypher: str, params: Any = None, **_kw: Any) -> tuple[list[dict], None, None]:
        if "IN_COMMUNITY" in cypher:
            return ([{
                "community_id": "c1", "summary": "Testing and refactoring practices",
                "level": 0, "seed_overlap": 1, "top_entity_ids": ["refactoring"],
            }], None, None)
        if "MENTIONS" in cypher:
            return ([{"artifact_id": RELATED, "shared": 2}], None, None)
        if "UNWIND $ids AS aid" in cypher:
            return ([{"id": RELATED, "domain": self.related_domain, "filename": f"{RELATED}.md"}], None, None)
        return ([], None, None)

    def session(self) -> Any:
        class _Session:
            def __enter__(self) -> Any:
                return self

            def __exit__(self, *_exc: Any) -> None:
                return None

            def run(self, *_a: Any, **_k: Any) -> list:
                return []

        return _Session()


@pytest.fixture
def auto_mode(chroma, monkeypatch):  # noqa: F811 — the imported fixture
    def install(related_domain: str) -> None:
        monkeypatch.setattr(agents, "get_chroma", lambda: _Chroma())
        monkeypatch.setattr(agents, "get_neo4j", lambda: _Neo4j(related_domain))
        monkeypatch.setattr("config.features.RETRIEVAL_MODE", "auto", raising=False)
        monkeypatch.setattr("core.agents.query_agent._should_skip_graph", lambda *a, **k: False)

    return install


def _graph_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in result["results"] if r.get("graph_source")]


@pytest.mark.asyncio
async def test_a_thematic_query_gets_the_entity_expansion(auto_mode):
    auto_mode("coding")

    result = await _ask(query=THEMATIC, domains=["coding"], strict_domains=True, context_sources=KB_ONLY)

    assert [r["artifact_id"] for r in _graph_rows(result)] == [RELATED]
    assert result["graph_results"] == 1
    assert {r["domain"] for r in result["results"]} == {"coding"}


@pytest.mark.asyncio
async def test_graph_results_counts_no_row_the_scope_dropped(auto_mode):
    auto_mode("finance")

    result = await _ask(query=THEMATIC, domains=["coding"], strict_domains=True, context_sources=KB_ONLY)

    assert _graph_rows(result) == []
    assert result["graph_results"] == 0
    assert [r["artifact_id"] for r in result["results"]] == [SEED]
