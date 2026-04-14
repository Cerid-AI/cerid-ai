# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Open Library data source -- free book metadata search, no API key required."""
from __future__ import annotations

import re

import httpx

from errors import RetrievalError

from .base import DataSource, DataSourceResult, logger

_BOOK_RE = re.compile(
    r"\b(?:book|novel|author|wrote|written|published|publisher|ISBN|"
    r"edition|paperback|hardcover|bibliography|literary|literature|"
    r"fiction|non-fiction|memoir|biography|anthology|chapter|volume)\b",
    re.I,
)
_QUOTED_TITLE_RE = re.compile(r'"([^"]+)"')
_BY_AUTHOR_RE = re.compile(r"\bby\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+){0,3})\b")


class OpenLibrarySource(DataSource):
    name = "openlibrary"
    description = "Open Library -- free book metadata, author info, and ISBNs. No API key required."
    requires_api_key = False
    domains: list[str] = ["books", "research", "education"]

    def adapt_query(self, raw_query: str, keywords: list[str]) -> str:
        """Extract book titles (quoted) or author names."""
        titles = _QUOTED_TITLE_RE.findall(raw_query)
        if titles:
            return titles[0]
        author_match = _BY_AUTHOR_RE.search(raw_query)
        if author_match:
            return author_match.group(1)
        return " ".join(keywords[:3]) if keywords else raw_query

    def is_relevant(self, raw_query: str, keywords: list[str]) -> bool:
        """Only relevant for book/literature queries."""
        return bool(_BOOK_RE.search(raw_query))

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    "https://openlibrary.org/search.json",
                    params={"q": query, "limit": "3", "fields": "title,author_name,first_publish_year,isbn,key,subject"},
                )
                resp.raise_for_status()
                docs = resp.json().get("docs", [])

                results: list[DataSourceResult] = []
                for doc in docs[:3]:
                    title = doc.get("title", "")
                    authors = ", ".join(doc.get("author_name", [])[:3])
                    year = doc.get("first_publish_year", "")
                    subjects = ", ".join(doc.get("subject", [])[:5])
                    isbn_list = doc.get("isbn", [])
                    isbn = isbn_list[0] if isbn_list else ""
                    key = doc.get("key", "")

                    parts = [f"**{title}**"]
                    if authors:
                        parts.append(f"by {authors}")
                    if year:
                        parts.append(f"({year})")
                    if isbn:
                        parts.append(f"ISBN: {isbn}")
                    if subjects:
                        parts.append(f"Subjects: {subjects}")

                    results.append(DataSourceResult(
                        title=title,
                        content=" | ".join(parts),
                        source_url=f"https://openlibrary.org{key}" if key else "",
                        source_name="Open Library",
                        confidence=0.40,  # book metadata is weakly relevant to factual queries
                    ))

                return results
        except (RetrievalError, httpx.HTTPError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
            logger.debug("Open Library query failed: %s", exc)
            return []
