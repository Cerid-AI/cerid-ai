# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Wikipedia data source -- free, no API key required."""
from __future__ import annotations

import re

import httpx

from errors import RetrievalError

from .base import DataSource, DataSourceResult, logger

_PROPER_NOUN_RE = re.compile(r"\b([A-Z][a-z]{2,}(?:\s[A-Z][a-z]{2,}){0,4})\b")

# Lightweight tokenizer for query↔result overlap scoring. Alphanumeric runs,
# lowercased, minus common stop words and tokens ≤2 chars. Stays zero-dep on
# purpose — spaCy / tiktoken would be overkill inside a per-result scoring hot
# path that runs for every Wikipedia hit on every RAG call.
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_STOP_WORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "any", "can",
    "had", "has", "have", "her", "his", "its", "may", "one", "our", "out",
    "she", "was", "way", "were", "will", "with", "your", "this", "that",
    "these", "those", "what", "when", "where", "why", "how", "who", "which",
    "into", "from", "about", "above", "below", "between", "among", "does",
    "did", "would", "could", "should", "must", "shall", "might", "been",
    "being", "they", "them", "their", "there", "then", "than",
})


def _content_tokens(text: str) -> set[str]:
    """Return lowercase significant tokens from ``text``.

    Significant = alphanumeric, >2 chars, not a stop word. The >2 cutoff drops
    common fillers ("is", "of", "to") without spaCy-weight tokenization.
    """
    return {
        t.lower() for t in _TOKEN_RE.findall(text)
        if len(t) > 2 and t.lower() not in _STOP_WORDS
    }


class WikipediaSource(DataSource):
    name = "wikipedia"
    description = "Wikipedia -- free encyclopedia. No API key required."
    requires_api_key = False
    domains: list[str] = []  # all domains

    _QUESTION_WORDS = {"What", "When", "Where", "Who", "Why", "How", "Which", "Does", "Can", "Could", "Would", "Should", "Are", "Were", "Was", "The"}

    def score_confidence(self, raw_query: str, result: "DataSourceResult") -> float:
        """Scale confidence by query-to-title+summary token overlap.

        Three-tier scoring, strongest signal first:

        1. **Title-in-query short-circuit** — if the title appears verbatim as
           a substring of the query (e.g. "Tokyo" in "what is the population
           of Tokyo?"), that's a high-confidence entity match. Apply the +0.05
           boost and skip overlap scaling; the query is clearly about this.
        2. **Token-overlap gradient** — Wikipedia search for "best rag" returns
           "Ragtime" with the same 0.85 as a semantically valid hit. Without
           gradient scoring, these pollute the context window. Scale base
           confidence by query↔(title+content) token overlap: zero overlap →
           30% of base (0.15 floor); full overlap → unchanged. Short queries
           (<2 significant tokens) skip this tier — too little signal.
        3. **Disambiguation / stub penalty** — unchanged.
        """
        title_lower = result.title.lower()
        query_lower = raw_query.lower()

        # Tier 1: strong entity match dominates lexical gradient.
        if title_lower and title_lower in query_lower:
            return min(1.0, result.confidence + 0.05)

        # Tier 2: gradient overlap scoring for everything else.
        base = result.confidence
        q_tokens = _content_tokens(raw_query)
        if len(q_tokens) >= 2:
            c_tokens = _content_tokens(f"{result.title} {result.content[:500]}")
            overlap = len(q_tokens & c_tokens) / len(q_tokens)
            base = max(0.15, base * (0.3 + 0.7 * overlap))

        # Tier 3: disambiguation / stub penalty.
        if "(disambiguation)" in title_lower or len(result.content) < 50:
            return max(0.0, base - 0.15)
        return base

    def adapt_query(self, raw_query: str, keywords: list[str]) -> str:
        """Wikipedia works best with entity names rather than keyword soup."""
        entities = _PROPER_NOUN_RE.findall(raw_query)
        # Filter question/article words that are only capitalized because they start a sentence
        entities = [e for e in entities if e not in self._QUESTION_WORDS]
        if entities:
            return " ".join(entities[:3])
        return " ".join(keywords[:3]) if keywords else raw_query

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        try:
            async with httpx.AsyncClient(
                timeout=5.0,
                headers={"User-Agent": "CeridAI/0.82 (https://github.com/Cerid-AI/cerid-ai)"},
            ) as client:
                resp = await client.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={"action": "query", "list": "search", "srsearch": query,
                            "srlimit": "3", "format": "json"},
                )
                resp.raise_for_status()
                search_results = resp.json().get("query", {}).get("search", [])

                results = []
                for sr in search_results[:2]:
                    title = sr.get("title", "")
                    summary_resp = await client.get(
                        f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
                    )
                    if summary_resp.status_code == 200:
                        data = summary_resp.json()
                        results.append(DataSourceResult(
                            title=data.get("title", title),
                            content=data.get("extract", ""),
                            source_url=data.get("content_urls", {}).get("desktop", {}).get("page", ""),
                            source_name="Wikipedia",
                            confidence=0.85,
                        ))
                return results
        except (RetrievalError, httpx.HTTPError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            logger.debug("Wikipedia query failed: %s", exc)
            return []
