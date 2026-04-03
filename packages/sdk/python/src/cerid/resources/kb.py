# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Knowledge-base resource methods: query, search, ingest, collections, taxonomy."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from cerid.errors import _raise_for_status
from cerid.models import (
    CollectionsResponse,
    IngestResponse,
    QueryResponse,
    SearchResponse,
    TaxonomyResponse,
)

if TYPE_CHECKING:
    import httpx

    from cerid._base import _BaseClient


class KBResource:
    """Synchronous knowledge-base operations."""

    def __init__(self, client: _BaseClient, http: httpx.Client) -> None:
        self._client = client
        self._http = http

    def query(
        self,
        query: str,
        *,
        domains: Optional[List[str]] = None,
        top_k: int = 5,
        conversation_id: Optional[str] = None,
    ) -> QueryResponse:
        """Multi-domain KB search with hybrid BM25+vector retrieval."""
        body = self._client._build_json(
            query=query,
            domains=domains,
            top_k=top_k,
            conversation_id=conversation_id,
        )
        resp = self._http.post(self._client._url("/query"), json=body)
        _raise_for_status(resp)
        return QueryResponse.model_validate(resp.json())

    def search(
        self,
        query: str,
        *,
        domain: str = "general",
        top_k: int = 5,
    ) -> SearchResponse:
        """Raw vector search without agent orchestration."""
        body = self._client._build_json(query=query, domain=domain, top_k=top_k)
        resp = self._http.post(self._client._url("/search"), json=body)
        _raise_for_status(resp)
        return SearchResponse.model_validate(resp.json())

    def ingest(
        self,
        content: str,
        *,
        domain: str = "general",
        tags: str = "",
    ) -> IngestResponse:
        """Ingest raw text content into the knowledge base."""
        body = self._client._build_json(content=content, domain=domain, tags=tags)
        resp = self._http.post(self._client._url("/ingest"), json=body)
        _raise_for_status(resp)
        return IngestResponse.model_validate(resp.json())

    def ingest_file(
        self,
        file_path: str,
        *,
        domain: str = "",
        tags: str = "",
        categorize_mode: str = "",
    ) -> IngestResponse:
        """Ingest a file from the archive or an absolute path."""
        body = self._client._build_json(
            file_path=file_path,
            domain=domain,
            tags=tags,
            categorize_mode=categorize_mode,
        )
        resp = self._http.post(self._client._url("/ingest/file"), json=body)
        _raise_for_status(resp)
        return IngestResponse.model_validate(resp.json())

    def collections(self) -> CollectionsResponse:
        """List all knowledge base collections."""
        resp = self._http.get(self._client._url("/collections"))
        _raise_for_status(resp)
        return CollectionsResponse.model_validate(resp.json())

    def taxonomy(self) -> TaxonomyResponse:
        """Get the domain taxonomy tree."""
        resp = self._http.get(self._client._url("/taxonomy"))
        _raise_for_status(resp)
        return TaxonomyResponse.model_validate(resp.json())


class AsyncKBResource:
    """Asynchronous knowledge-base operations."""

    def __init__(self, client: _BaseClient, http: httpx.AsyncClient) -> None:
        self._client = client
        self._http = http

    async def query(
        self,
        query: str,
        *,
        domains: Optional[List[str]] = None,
        top_k: int = 5,
        conversation_id: Optional[str] = None,
    ) -> QueryResponse:
        """Multi-domain KB search with hybrid BM25+vector retrieval."""
        body = self._client._build_json(
            query=query,
            domains=domains,
            top_k=top_k,
            conversation_id=conversation_id,
        )
        resp = await self._http.post(self._client._url("/query"), json=body)
        _raise_for_status(resp)
        return QueryResponse.model_validate(resp.json())

    async def search(
        self,
        query: str,
        *,
        domain: str = "general",
        top_k: int = 5,
    ) -> SearchResponse:
        """Raw vector search without agent orchestration."""
        body = self._client._build_json(query=query, domain=domain, top_k=top_k)
        resp = await self._http.post(self._client._url("/search"), json=body)
        _raise_for_status(resp)
        return SearchResponse.model_validate(resp.json())

    async def ingest(
        self,
        content: str,
        *,
        domain: str = "general",
        tags: str = "",
    ) -> IngestResponse:
        """Ingest raw text content into the knowledge base."""
        body = self._client._build_json(content=content, domain=domain, tags=tags)
        resp = await self._http.post(self._client._url("/ingest"), json=body)
        _raise_for_status(resp)
        return IngestResponse.model_validate(resp.json())

    async def ingest_file(
        self,
        file_path: str,
        *,
        domain: str = "",
        tags: str = "",
        categorize_mode: str = "",
    ) -> IngestResponse:
        """Ingest a file from the archive or an absolute path."""
        body = self._client._build_json(
            file_path=file_path,
            domain=domain,
            tags=tags,
            categorize_mode=categorize_mode,
        )
        resp = await self._http.post(self._client._url("/ingest/file"), json=body)
        _raise_for_status(resp)
        return IngestResponse.model_validate(resp.json())

    async def collections(self) -> CollectionsResponse:
        """List all knowledge base collections."""
        resp = await self._http.get(self._client._url("/collections"))
        _raise_for_status(resp)
        return CollectionsResponse.model_validate(resp.json())

    async def taxonomy(self) -> TaxonomyResponse:
        """Get the domain taxonomy tree."""
        resp = await self._http.get(self._client._url("/taxonomy"))
        _raise_for_status(resp)
        return TaxonomyResponse.model_validate(resp.json())
