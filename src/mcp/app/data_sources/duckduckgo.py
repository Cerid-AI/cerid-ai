# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""DuckDuckGo Instant Answers data source -- free, no API key required."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx

from .base import DataSource, DataSourceResult

_MAX_RELATED_TOPICS = 2
_QUOTED_RE = re.compile(r'"([^"]+)"')
# Runs of capitalised words: the named topic in a natural-language question.
_CAP_RUN_RE = re.compile(r"\b[A-Z][A-Za-z0-9'-]+(?:\s+[A-Z][A-Za-z0-9'-]+)*")
_QUESTION_OPENERS = frozenset({
    "what", "who", "when", "where", "why", "how", "which", "is", "are", "does",
    "do", "did", "can", "could", "should", "would", "was", "were", "tell",
    "give", "show", "find", "explain", "define", "describe", "compare", "list",
    "the", "please",
})


class DuckDuckGoSource(DataSource):
    name = "duckduckgo"
    description = "DuckDuckGo Instant Answers -- quick answers, related topics, abstracts. No API key required."
    requires_api_key = False
    domains: list[str] = []  # all domains
    # Off unless the user turns it on: across the 2026-10-11 audit runs the
    # Instant Answer API returned no row the cross-encoder accepted (37
    # questions, 0 rows; tangents such as 'Anders Celsius' otherwise), while
    # web search already reaches DuckDuckGo as a SearXNG engine.
    enabled = False

    def score_confidence(self, raw_query: str, result: "DataSourceResult") -> float:
        """Boost .gov/.edu source URLs; reduce for tangential related topics."""
        host = (urlsplit(result.source_url).hostname or "").rstrip(".")
        if host.endswith((".gov", ".edu")):
            return min(1.0, result.confidence + 0.10)
        # Related topics with very short content are likely tangential
        if len(result.content) < 30:
            return max(0.0, result.confidence - 0.10)
        return result.confidence

    def adapt_query(self, raw_query: str, keywords: list[str]) -> str:
        """Reduce the query to one topic.

        The Instant Answer API looks up a single entity ("Tokyo", "aspirin")
        and answers a multi-word keyword bag with an all-empty payload, so the
        old "up to four keywords" string returned nothing on every real query.
        Preference: a quoted phrase, then a capitalised run that is not the
        question opener, then the last keyword — the extractor keeps first-
        occurrence order, so in "population of tokyo" that is the subject.
        """
        quoted = _QUOTED_RE.findall(raw_query)
        if quoted:
            return quoted[0]
        runs = [
            run for run in _CAP_RUN_RE.findall(raw_query)
            if run.split()[0].lower() not in _QUESTION_OPENERS
        ]
        if runs:
            return max(runs, key=len)
        return keywords[-1] if keywords else raw_query

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Removed skip_disambig — disambiguation pages contain useful structured data
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1"},
            )
            resp.raise_for_status()
            data = resp.json()

            results: list[DataSourceResult] = []

            # Abstract (instant answer)
            abstract = data.get("AbstractText", "")
            if abstract:
                results.append(DataSourceResult(
                    title=data.get("Heading", query),
                    content=abstract,
                    source_url=data.get("AbstractURL", ""),
                    source_name="DuckDuckGo",
                    confidence=0.80,
                ))

            # Related topics (up to 2). A group entry holds a "Topics" list and
            # no text of its own; filtered before the cut, so groups cannot
            # crowd out the flat topics behind them.
            flat_topics = [
                t for t in data.get("RelatedTopics", [])
                if t.get("Text") and not isinstance(t.get("Topics"), list)
            ]
            for topic in flat_topics[:_MAX_RELATED_TOPICS]:
                text = topic["Text"]
                results.append(DataSourceResult(
                    title=text[:80],
                    content=text,
                    source_url=topic.get("FirstURL", ""),
                    source_name="DuckDuckGo",
                    confidence=0.70,
                ))

            return results
