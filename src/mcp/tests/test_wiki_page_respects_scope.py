# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A compiled wiki page reaches a scoped query only if the query may read
every domain the page was compiled from (audit F06).

Live probe 2026-10-10: a strict ``coding`` question was handed the compiled
page for dollar-cost averaging on /agent/query (manual and smart) and
/sdk/v1/query. The wiki surface was the one row source no scope reached.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.agents.query_agent import _recall_wiki_surface, set_wiki_page_fetcher
from tests.test_named_domains_are_a_hard_scope import (  # noqa: F401 — chroma is a fixture
    KB_ONLY,
    _ask,
    chroma,
)

QUERY = "what is dollar cost averaging"
pytestmark = pytest.mark.usefixtures("chroma")


def _page(domains: list[str] | None, primary: str | None = None) -> dict[str, Any]:
    return {
        "content": "Dollar-cost averaging buys a fixed amount at regular intervals.",
        "title": "Dollar-cost averaging", "slug": "other:dollar-cost-averaging",
        "domains": domains, "primary_domain": primary,
    }


@pytest.fixture
def wiki_page():
    def install(page: dict[str, Any]) -> AsyncMock:
        fetcher = AsyncMock(return_value=page)
        set_wiki_page_fetcher(fetcher)
        return fetcher

    yield install
    set_wiki_page_fetcher(None)


def _wiki_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in result["results"] if r.get("source_type") == "wiki"]


@pytest.mark.asyncio
async def test_a_strict_query_is_not_handed_a_page_from_another_domain(wiki_page):
    wiki_page(_page(["finance"], "finance"))

    result = await _ask(query=QUERY, domains=["coding"], strict_domains=True, context_sources=KB_ONLY)

    assert _wiki_rows(result) == []
    assert {r["domain"] for r in result["results"]} <= {"coding"}


@pytest.mark.asyncio
async def test_a_strict_query_keeps_a_page_from_its_own_domain(wiki_page):
    wiki_page(_page(["coding"], "coding"))

    result = await _ask(query=QUERY, domains=["coding"], strict_domains=True, context_sources=KB_ONLY)

    assert [r["domain"] for r in _wiki_rows(result)] == ["coding"]


@pytest.mark.asyncio
async def test_a_restricted_consumer_is_not_handed_a_page_outside_its_wall(wiki_page):
    wiki_page(_page(["coding", "finance"], "coding"))

    result = await _ask("cerid-finance", query=QUERY, domains=["finance"], context_sources=KB_ONLY)

    assert _wiki_rows(result) == []


@pytest.mark.asyncio
async def test_an_unscoped_query_is_not_handed_a_page_from_a_hidden_domain(wiki_page, monkeypatch):
    """Sensitive domains (iMessage) need an opt-in; a page compiled from one is
    as hidden as the domain itself."""
    monkeypatch.setattr("core.agents.query_agent.sensitive_domains_opted_in", lambda: False)
    wiki_page(_page(["imessage"], "imessage"))

    result = await _ask(query=QUERY, context_sources=KB_ONLY)

    assert _wiki_rows(result) == []


@pytest.mark.asyncio
async def test_an_unscoped_query_still_gets_the_page(wiki_page):
    wiki_page(_page(["finance"], "finance"))

    result = await _ask(query=QUERY, context_sources=KB_ONLY)

    assert len(_wiki_rows(result)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("domains", [None, []])
async def test_a_page_with_no_recorded_domains_stays_out_of_a_scoped_query(wiki_page, domains):
    wiki_page(_page(domains))

    assert await _recall_wiki_surface("dollar cost averaging", scope={"coding"}) == []
    assert len(await _recall_wiki_surface("dollar cost averaging")) == 1
