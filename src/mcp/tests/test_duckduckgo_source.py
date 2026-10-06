# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Why DuckDuckGo returned nothing on every query, pinned offline.

Round 5 item 5.9 / Studio audit W7. The Instant Answer API is a topic lookup,
not a search engine: it answers a single entity ("Tokyo", "aspirin") and
returns an all-empty payload for any multi-word bag of keywords. The old
``adapt_query`` sent up to four keywords joined by spaces — "current
population tokyo" — so every real query hit the empty branch. The payloads
below were captured from https://api.duckduckgo.com on 2026-10-05 (status 202
with the httpx user agent, body intact; 200 with a browser agent) and trimmed
to the fields the client reads. No test here talks to the network.
"""
from __future__ import annotations

import httpx
import pytest

from app.data_sources.duckduckgo import DuckDuckGoSource

# q=current+population+tokyo — every field empty, no related topics.
_EMPTY = {
    "Abstract": "", "AbstractText": "", "AbstractURL": "", "Heading": "",
    "RelatedTopics": [], "Results": [], "Type": "",
}

# q=Tokyo — a disambiguation page: three plain topics, then grouped ones.
_TOKYO = {
    "Abstract": "", "AbstractText": "", "AbstractURL": "", "Heading": "Tokyo",
    "Type": "D",
    "RelatedTopics": [
        {"FirstURL": "https://duckduckgo.com/Tokyo",
         "Text": "Tokyo The capital and most populous city of Japan."},
        {"FirstURL": "https://duckduckgo.com/Greater_Tokyo_Area",
         "Text": "Greater Tokyo Area The third most populous metropolitan area in the world."},
        {"FirstURL": "https://duckduckgo.com/Special_wards_of_Tokyo",
         "Text": "Special wards of Tokyo A unique form of municipality."},
        {"Name": "Places", "Topics": [
            {"FirstURL": "https://duckduckgo.com/Tokyo_Bay", "Text": "Tokyo Bay A bay in Japan."},
        ]},
    ],
}


@pytest.fixture
def ddg_api(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params.get("q", "")
        seen.append(q)
        body = _TOKYO if q == "Tokyo" else _EMPTY
        return httpx.Response(202, json=body)

    real_client = httpx.AsyncClient

    def patched_client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)
    return seen


@pytest.mark.asyncio
async def test_a_keyword_bag_gets_the_empty_payload(ddg_api):
    results = await DuckDuckGoSource().query("current population tokyo")
    assert results == []
    assert ddg_api == ["current population tokyo"]


def test_adapt_query_sends_the_named_topic_not_the_bag():
    src = DuckDuckGoSource()
    assert src.adapt_query(
        "What is the current population of Tokyo?", ["current", "population", "tokyo"],
    ) == "Tokyo"


def test_adapt_query_prefers_a_quoted_phrase():
    src = DuckDuckGoSource()
    assert src.adapt_query('tell me about "Greater Tokyo Area"', ["tell", "greater", "tokyo", "area"]) == "Greater Tokyo Area"


def test_adapt_query_falls_back_to_the_last_keyword_for_a_lowercase_question():
    src = DuckDuckGoSource()
    assert src.adapt_query(
        "what is the molecular formula of caffeine", ["molecular", "formula", "caffeine"],
    ) == "caffeine"


@pytest.mark.asyncio
async def test_a_single_topic_yields_results(ddg_api):
    results = await DuckDuckGoSource().query("Tokyo")
    assert ddg_api == ["Tokyo"]
    assert [r.title for r in results] == [
        "Tokyo The capital and most populous city of Japan.",
        "Greater Tokyo Area The third most populous metropolitan area in the world.",
    ]
    assert all(r.source_name == "DuckDuckGo" for r in results)
