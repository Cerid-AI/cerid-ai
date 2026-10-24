# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""CRAG hands each external source the question, not only its keywords (F111).

Live probe: CRAG called ``query_all`` with the keyword string alone, so every
source's ``adapt_query`` saw 'wrote novel middlemarch' in place of the question.
Wikipedia's entity match needs capitals and found none; web search was asked
'happened news week' for 'What happened in AI news this week?', because the
extractor drops two-letter words. Smart mode already passed the question.
The registry, the sources' adapters and the CRAG path are real; only the
network call is faked.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.agents.retrieval_orchestrator import _extract_search_terms
from app.data_sources.base import DataSourceRegistry
from app.data_sources.web_search_source import WebSearchDataSource
from app.data_sources.wikipedia import WikipediaSource
from core.agents import crag


async def _asked(question: str, monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("SEARXNG_URL", "http://searxng.test")
    asked: dict[str, str] = {}
    registry = DataSourceRegistry()
    for source in (WikipediaSource(), WebSearchDataSource()):
        source.enabled = True

        async def query(q, _name=source.name, **_):
            asked[_name] = q
            return []
        source.query = query
        registry.register(source)
    with (
        patch.object(crag, "_external_registry", registry),
        patch.object(crag, "_search_term_extractor", _extract_search_terms),
        patch("core.routing.egress.cloud_egress_refused", return_value=False),
    ):
        await crag.augment_external_crag({"results": []}, question, None, 0.6)
    return asked


@pytest.mark.asyncio
async def test_wikipedia_gets_the_entity_from_the_question(monkeypatch):
    asked = await _asked("Who wrote the novel Middlemarch?", monkeypatch)
    assert asked["wikipedia"] == "Middlemarch"


@pytest.mark.asyncio
async def test_web_search_gets_the_question_with_its_short_words(monkeypatch):
    asked = await _asked("What happened in AI news this week?", monkeypatch)
    assert asked["web_search"] == "What happened in AI news this week?"
