# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Asynchronous Cerid AI SDK client."""

from __future__ import annotations

from typing import Optional

import httpx

from cerid._base import DEFAULT_TIMEOUT, _BaseClient
from cerid.resources.kb import AsyncKBResource
from cerid.resources.memory import AsyncMemoryResource
from cerid.resources.system import AsyncSystemResource
from cerid.resources.verify import AsyncVerifyResource


class AsyncCeridClient(_BaseClient):
    """Asynchronous client for the Cerid AI SDK API.

    Usage::

        import asyncio
        from cerid import AsyncCeridClient

        async def main():
            async with AsyncCeridClient(
                base_url="http://localhost:8888",
                client_id="my-app",
            ) as client:
                result = await client.kb.query("How does chunking work?")
                print(result.context)

        asyncio.run(main())
    """

    def __init__(
        self,
        base_url: str,
        client_id: str,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(base_url, client_id, api_key, timeout)
        self._http = httpx.AsyncClient(
            headers=self._build_headers(),
            timeout=self.timeout,
        )
        self._kb: Optional[AsyncKBResource] = None
        self._verify: Optional[AsyncVerifyResource] = None
        self._memory: Optional[AsyncMemoryResource] = None
        self._system: Optional[AsyncSystemResource] = None

    # -- Resource properties (lazy-initialized) --

    @property
    def kb(self) -> AsyncKBResource:
        """Knowledge-base operations: query, search, ingest, collections, taxonomy."""
        if self._kb is None:
            self._kb = AsyncKBResource(self, self._http)
        return self._kb

    @property
    def verify(self) -> AsyncVerifyResource:
        """Verification operations: hallucination detection."""
        if self._verify is None:
            self._verify = AsyncVerifyResource(self, self._http)
        return self._verify

    @property
    def memory(self) -> AsyncMemoryResource:
        """Memory operations: extraction and storage."""
        if self._memory is None:
            self._memory = AsyncMemoryResource(self, self._http)
        return self._memory

    @property
    def system(self) -> AsyncSystemResource:
        """System operations: health, settings, plugins."""
        if self._system is None:
            self._system = AsyncSystemResource(self, self._http)
        return self._system

    # -- Lifecycle --

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._http.aclose()

    async def __aenter__(self) -> AsyncCeridClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
