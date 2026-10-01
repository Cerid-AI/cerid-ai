# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A request that names its domains is answered from those domains only.

Live stack: POST /agent/query with domains ["tasks"], X-Client-ID gui and the
knowledge base as the only source returned rows from ``tasks`` and ``finance``.
The ``gui`` consumer is registered non-strict, so naming a domain without
``query_scope`` still pulled the other owner domains in at the affinity weight.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import config
from app.routers import agents
from app.routers.agents import AgentQueryRequest
from app.startup.domain_rehydration import merge_persisted_domains
from utils.domain_privacy import owner_domains, sensitive_domains_opted_in, visible_domains

QUERY = "which invoices are still waiting on the garden project"
KB_ONLY = {"kb": True, "memory": False, "external": False}
# Routed to the memory surface: a first-person question about a decision.
RECALL_QUERY = "what did we decide about the garden project invoices"
MEMORY_TEXT = "We decided to pay the garden project invoices at the end of the month."
NO_WEB = {"external": False}


class _FixtureChromaClient:
    """Every domain holds one close match, so a widened scan has rows to return."""

    def __init__(self) -> None:
        self.opened: list[str] = []
        self.recalled: list[str] = []

    def list_collections(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=config.collection_name(d)) for d in config.DOMAINS]

    def get_collection(self, name: str) -> Any:
        self.opened.append(name)

        def query(**_kw: Any) -> dict[str, Any]:
            return {
                "ids": [[f"{name}-chunk-0"]],
                "documents": [[f"garden project invoices waiting, filed under {name}"]],
                "metadatas": [[{
                    "artifact_id": f"{name}-artifact",
                    "filename": f"{name}.md",
                    "chunk_index": 0,
                }]],
                "distances": [[0.2]],
            }

        return SimpleNamespace(query=query, count=lambda: 1)

    def get_or_create_collection(self, name: str) -> Any:
        """The door memory recall uses; the knowledge base scan never does."""
        self.recalled.append(name)

        def query(**_kw: Any) -> dict[str, Any]:
            return {
                "ids": [["memory-chunk-0"]],
                "documents": [[MEMORY_TEXT]],
                "metadatas": [[{
                    "artifact_id": "memory-artifact",
                    "memory_type": "decision",
                    "summary": MEMORY_TEXT,
                }]],
                "distances": [[0.1]],
            }

        return SimpleNamespace(query=query)


def _request(client_id: str | None = "gui") -> Any:
    async def is_disconnected() -> bool:
        return False

    headers = {"x-client-id": client_id} if client_id else {}
    return SimpleNamespace(headers=headers, is_disconnected=is_disconnected)


@pytest.fixture
def chroma(monkeypatch: pytest.MonkeyPatch) -> _FixtureChromaClient:
    from core.retrieval import bm25 as bm25_mod

    client = _FixtureChromaClient()
    monkeypatch.setattr(bm25_mod, "is_available", lambda: False)
    # ``tasks`` is an operator-made domain: boot merges it into config.DOMAINS.
    monkeypatch.setattr(config, "TAXONOMY", dict(config.TAXONOMY))
    monkeypatch.setattr(config, "DOMAINS", list(config.DOMAINS))
    merge_persisted_domains([
        {"name": "tasks", "description": "", "icon": "", "sub_categories": []},
    ])
    monkeypatch.setattr(agents, "get_chroma", lambda: client)
    monkeypatch.setattr(agents, "get_redis", lambda: None)
    monkeypatch.setattr(agents, "get_neo4j", lambda: None)
    monkeypatch.setattr(agents, "get_graph_store", lambda: None)
    # The junk floor is not the scope control: live, the widened rows cleared it.
    monkeypatch.setattr(config, "QUALITY_MIN_RELEVANCE_THRESHOLD", 0.0)
    # The route records a quality metric through Redis once it has rows.
    monkeypatch.setattr(
        "utils.metrics.get_metrics_collector",
        lambda: SimpleNamespace(record_metric=lambda *a, **kw: None),
    )
    return client


@pytest.fixture
def passed(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """What the route hands to retrieval, without running it."""
    from core.agents import query_agent

    seen: dict[str, Any] = {}

    async def fake_agent_query_full(query: str, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {
            "context": "", "sources": [], "results": [], "confidence": 0.0,
            "domains_searched": kwargs.get("domains") or [], "total_results": 0,
        }

    monkeypatch.setattr(query_agent, "agent_query_full", fake_agent_query_full)
    for name in ("get_chroma", "get_redis", "get_neo4j", "get_graph_store"):
        monkeypatch.setattr(agents, name, lambda: None)
    return seen


async def _ask(
    client_id: str | None = "gui", query: str = QUERY, **fields: Any,
) -> dict[str, Any]:
    req = AgentQueryRequest(query=query, use_reranking=False, skip_cache=True, **fields)
    return await agents._agent_query_inner(req, _request(client_id))


def _memory_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in result["results"] if r.get("source_type") == "memory"]


def _kb_domains(result: dict[str, Any]) -> set[str]:
    return {r["domain"] for r in result["results"] if r.get("source_type") != "memory"}


def _domains_of(result: dict[str, Any]) -> set[str]:
    return {r["domain"] for r in result["results"]}


@pytest.mark.asyncio
async def test_a_gui_query_naming_tasks_returns_rows_from_tasks_only(
    chroma: _FixtureChromaClient,
) -> None:
    result = await _ask(domains=["tasks"], context_sources=KB_ONLY)

    assert _domains_of(result) == {"tasks"}
    assert set(chroma.opened) == {config.collection_name("tasks")}
    assert not any(r.get("cross_domain") for r in result["results"])


@pytest.mark.asyncio
async def test_a_query_with_no_client_id_is_the_gui_and_is_held_the_same_way(
    chroma: _FixtureChromaClient,
) -> None:
    result = await _ask(None, domains=["tasks"], context_sources=KB_ONLY)

    assert _domains_of(result) == {"tasks"}


@pytest.mark.asyncio
async def test_naming_two_domains_searches_both_and_nothing_else(
    chroma: _FixtureChromaClient,
) -> None:
    result = await _ask(domains=["tasks", "finance"], top_k=10, context_sources=KB_ONLY)

    assert _domains_of(result) == {"tasks", "finance"}
    assert set(chroma.opened) == {
        config.collection_name("tasks"), config.collection_name("finance"),
    }


@pytest.mark.asyncio
async def test_an_unscoped_gui_query_still_reaches_every_owner_domain(
    chroma: _FixtureChromaClient,
) -> None:
    result = await _ask(top_k=50, context_sources=KB_ONLY)

    everything = visible_domains(
        owner_domains(), include_sensitive=sensitive_domains_opted_in(),
    )
    assert len(everything) > 2
    assert set(chroma.opened) == {config.collection_name(d) for d in everything}
    assert _domains_of(result) == set(everything)


@pytest.mark.asyncio
async def test_naming_a_domain_keeps_memory_and_leaves_the_web_as_asked(
    passed: dict[str, Any],
) -> None:
    await _ask(domains=["tasks"])

    assert passed["strict_domains"] is True
    assert passed["memory_enabled"] is True
    assert passed["external_augmentation"] is True


@pytest.mark.asyncio
async def test_naming_a_domain_does_not_turn_on_a_memory_the_caller_turned_off(
    passed: dict[str, Any],
) -> None:
    await _ask(domains=["tasks"], context_sources={"memory": False})

    assert passed["memory_enabled"] is False


@pytest.mark.asyncio
async def test_asking_for_strict_domains_still_turns_memory_and_the_web_off(
    passed: dict[str, Any],
) -> None:
    await _ask(domains=["tasks"], strict_domains=True)

    assert passed["strict_domains"] is True
    assert passed["memory_enabled"] is False
    assert passed["external_augmentation"] is False


@pytest.mark.asyncio
async def test_naming_a_domain_does_not_turn_on_a_web_the_caller_turned_off(
    passed: dict[str, Any],
) -> None:
    await _ask(domains=["tasks"], context_sources={"external": False})

    assert passed["external_augmentation"] is False


@pytest.mark.asyncio
async def test_memory_stays_when_conversations_is_a_named_domain(
    passed: dict[str, Any],
) -> None:
    await _ask(domains=["tasks", "conversations"])

    assert passed["strict_domains"] is True
    assert passed["memory_enabled"] is True
    assert passed["external_augmentation"] is True


@pytest.mark.asyncio
async def test_asking_for_the_domain_scope_still_turns_the_web_off(
    passed: dict[str, Any],
) -> None:
    await _ask(domains=["tasks"], query_scope="domain")

    assert passed["strict_domains"] is True
    assert passed["memory_enabled"] is False
    assert passed["external_augmentation"] is False


@pytest.mark.asyncio
async def test_an_unscoped_gui_query_keeps_memory_the_web_and_the_loose_scope(
    passed: dict[str, Any],
) -> None:
    await _ask()

    assert not passed["strict_domains"]
    assert passed["domains"] is None
    assert passed["allowed_domains"] is None
    assert passed["memory_enabled"] is True
    assert passed["external_augmentation"] is True


@pytest.mark.asyncio
async def test_an_empty_domain_list_names_nothing(passed: dict[str, Any]) -> None:
    await _ask(domains=[])

    assert not passed["strict_domains"]
    assert passed["memory_enabled"] is True
    assert passed["external_augmentation"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("domains", [["finance"], None])
async def test_a_consumer_strict_by_registration_is_unchanged(
    passed: dict[str, Any], domains: list[str] | None,
) -> None:
    await _ask("cerid-finance", domains=domains)

    assert passed["strict_domains"] is True
    assert passed["allowed_domains"] == ["finance"]
    assert passed["domains"] == domains
    # #471: memory goes off for a strict scope with named domains, the web stays.
    assert passed["memory_enabled"] is (domains is None)
    assert passed["external_augmentation"] is True


@pytest.mark.asyncio
async def test_the_smart_path_is_held_to_the_named_domains_too(
    monkeypatch: pytest.MonkeyPatch, passed: dict[str, Any],
) -> None:
    from app.agents import retrieval_orchestrator

    seen: dict[str, Any] = {}

    async def fake_orchestrated_query(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"context": "", "sources": [], "results": [], "confidence": 0.0}

    monkeypatch.setattr(retrieval_orchestrator, "orchestrated_query", fake_orchestrated_query)

    await _ask(domains=["tasks"], rag_mode="smart")

    assert seen["strict_domains"] is True
    assert seen["context_sources"] is None


@pytest.mark.asyncio
async def test_the_smart_path_still_loses_memory_when_the_request_asks_for_the_scope(
    monkeypatch: pytest.MonkeyPatch, passed: dict[str, Any],
) -> None:
    from app.agents import retrieval_orchestrator

    seen: dict[str, Any] = {}

    async def fake_orchestrated_query(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"context": "", "sources": [], "results": [], "confidence": 0.0}

    monkeypatch.setattr(retrieval_orchestrator, "orchestrated_query", fake_orchestrated_query)

    await _ask(domains=["tasks"], rag_mode="smart", query_scope="domain")

    assert seen["strict_domains"] is True
    assert seen["context_sources"] == {"memory": False, "external": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("rag_mode", ["manual", "smart"])
async def test_a_recalled_memory_reaches_the_answer_of_a_gui_query_naming_tasks(
    chroma: _FixtureChromaClient, rag_mode: str,
) -> None:
    result = await _ask(
        query=RECALL_QUERY, domains=["tasks"], rag_mode=rag_mode, context_sources=NO_WEB,
    )

    assert [r["content"] for r in _memory_rows(result)] == [MEMORY_TEXT]
    assert chroma.recalled == [config.collection_name("conversations")]
    # The knowledge base rows are still held to the named domain.
    assert _kb_domains(result) == {"tasks"}
    assert set(chroma.opened) == {config.collection_name("tasks")}
    assert not any(r.get("cross_domain") for r in result["results"])


@pytest.mark.asyncio
@pytest.mark.parametrize("rag_mode", ["manual", "smart"])
@pytest.mark.parametrize(
    ("client_id", "domain", "fields"),
    [
        ("gui", "tasks", {"query_scope": "domain"}),
        ("gui", "tasks", {"strict_domains": True}),
        ("cerid-finance", "finance", {}),
    ],
)
async def test_no_memory_is_recalled_for_a_scope_that_is_strict_without_the_named_domains(
    chroma: _FixtureChromaClient,
    rag_mode: str,
    client_id: str,
    domain: str,
    fields: dict[str, Any],
) -> None:
    result = await _ask(
        client_id, query=RECALL_QUERY, domains=[domain], rag_mode=rag_mode,
        context_sources=NO_WEB, **fields,
    )

    assert _memory_rows(result) == []
    assert chroma.recalled == []
    assert _kb_domains(result) == {domain}
