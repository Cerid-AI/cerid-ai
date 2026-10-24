# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""A query retrieval skipped never goes to external sources. Measured on the
live stack (audit 2026-10-10): "hi" was skipped by the adaptive gate, then CRAG
read the empty result as low confidence and sent the greeting to Wikipedia,
DuckDuckGo and the web search — eight external queries for a hello. A
consumer-restricted query was sent out the same way."""
from __future__ import annotations

import asyncio

import pytest

from core.agents import crag
from core.retrieval.retrieval_gate import classify_retrieval_need


class _Registry:
    def __init__(self):
        self.calls = 0

    async def query_all(self, *a, **k):
        self.calls += 1
        return [{"content": "x", "url": "https://w/x", "source_name": "Wikipedia", "relevance": 0.9}]


@pytest.fixture
def registry():
    reg = _Registry()
    crag.set_external_source_registry(reg)
    crag.set_search_term_extractor(lambda q: q)
    yield reg
    crag.set_external_source_registry(None)
    crag.set_search_term_extractor(None)


@pytest.mark.parametrize("reason", ["conversational", "consumer_domain_restricted", "empty_query"])
def test_a_skipped_retrieval_sends_nothing_out(registry, reason):
    skipped = {"results": [], "sources": [], "retrieval_skipped": True, "retrieval_reason": reason}
    out = asyncio.run(crag.augment_external_crag(skipped, "hi", None, 0.6))
    assert registry.calls == 0 and out["results"] == []


def test_an_empty_but_unskipped_retrieval_still_goes_out(registry):
    out = asyncio.run(crag.augment_external_crag({"results": [], "sources": []}, "population of Lagos", None, 0.6))
    assert registry.calls == 1 and out["results"][0]["source_type"] == "external"


@pytest.mark.parametrize("greeting", ["good morning", "Good evening!", "good afternoon", "hi", "hello there"])
def test_greetings_skip_retrieval(greeting):
    assert classify_retrieval_need(greeting).action == "skip"


def test_a_question_starting_with_good_still_retrieves():
    assert classify_retrieval_need("good practices for database indexing").action != "skip"
