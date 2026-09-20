# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The sensitive-domain privacy filter is enforced at the retrieval chokepoint.

F018: ``utils.domain_privacy.visible_domains`` was applied by exactly one
caller (``pkb_search_filtered``), so ``/query``, ``/sdk/v1/search`` and the
chat pipeline all reached the ``domain_messages`` collection by a second
door. These tests drive the real ``multi_domain_query`` against a fake Chroma
client and assert on the collections it actually opens — so a future caller
that hands the raw request domains straight through cannot re-open the door.

F113: adjacent-domain expansion must not escape a consumer's
``allowed_domains`` isolation even when ``strict_domains`` is left False.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import config


def _empty_chroma_results() -> dict[str, Any]:
    return {
        "ids": [[]],
        "documents": [[]],
        "metadatas": [[]],
        "distances": [[]],
    }


class _RecordingChromaClient:
    """Records which domain collections were opened for a query."""

    def __init__(self) -> None:
        self.opened: list[str] = []

    def list_collections(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=config.collection_name(d)) for d in config.DOMAINS]

    def get_collection(self, name: str) -> Any:
        self.opened.append(name)
        return SimpleNamespace(query=lambda **kw: _empty_chroma_results())


@pytest.fixture
def _no_bm25(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.retrieval import bm25 as bm25_mod

    monkeypatch.setattr(bm25_mod, "is_available", lambda: False)


def _opt_in(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    import config.settings

    monkeypatch.setattr(config.settings, "SENSITIVE_DOMAIN_RETRIEVAL_ENABLED", value)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_bm25")
async def test_explicit_sensitive_domain_never_reaches_chroma(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """domains=["messages"] with the opt-in OFF must open no collection."""
    from core.agents import query_agent

    _opt_in(monkeypatch, False)
    client = _RecordingChromaClient()

    results = await query_agent.multi_domain_query(
        query="what did we say about the divorce",
        domains=["messages"],
        top_k=5,
        chroma_client=client,
    )

    assert client.opened == [], (
        f"privacy-gated domain reached ChromaDB: {client.opened!r}"
    )
    assert results == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_bm25")
async def test_all_domains_scan_skips_sensitive_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """domains=None means "every domain" — the gated ones are still gated."""
    from core.agents import query_agent

    _opt_in(monkeypatch, False)
    client = _RecordingChromaClient()

    await query_agent.multi_domain_query(
        query="anything",
        domains=None,
        top_k=3,
        chroma_client=client,
    )

    assert config.collection_name("messages") not in client.opened
    assert config.collection_name("general") in client.opened


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_bm25")
async def test_opt_in_restores_sensitive_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With SENSITIVE_DOMAIN_RETRIEVAL_ENABLED on, the domain is searched."""
    from core.agents import query_agent

    _opt_in(monkeypatch, True)
    client = _RecordingChromaClient()

    await query_agent.multi_domain_query(
        query="anything",
        domains=["messages"],
        top_k=3,
        chroma_client=client,
    )

    assert client.opened == [config.collection_name("messages")]


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_bm25")
async def test_agent_query_reports_only_visible_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The envelope must not claim a gated domain was searched."""
    from core.agents import query_agent

    _opt_in(monkeypatch, False)
    client = _RecordingChromaClient()

    result = await query_agent.agent_query(
        query="anything",
        domains=["messages"],
        top_k=3,
        use_reranking=False,
        chroma_client=client,
    )

    assert client.opened == [], f"gated domain reached ChromaDB: {client.opened!r}"
    assert result.get("domains_searched") == []
    assert result.get("sources") == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_bm25")
async def test_adjacent_expansion_respects_allowed_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F113: cross-domain bleed must stay inside the consumer's allow-list.

    ``strict_domains`` is left at its default False — the historical bypass.
    """
    from core.agents import query_agent

    _opt_in(monkeypatch, False)
    client = _RecordingChromaClient()

    await query_agent.agent_query(
        query="quarterly numbers",
        domains=["finance"],
        top_k=3,
        use_reranking=False,
        chroma_client=client,
        allowed_domains=["finance", "general"],
        strict_domains=False,
    )

    escaped = {
        name for name in client.opened
        if name not in {config.collection_name("finance"), config.collection_name("general")}
    }
    assert not escaped, f"adjacent-domain bleed escaped allowed_domains: {sorted(escaped)}"
