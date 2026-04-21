# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Synchronous Cerid AI SDK client."""

from __future__ import annotations

from typing import Optional

import httpx

from cerid._base import DEFAULT_TIMEOUT, _BaseClient
from cerid.resources.kb import KBResource
from cerid.resources.memory import MemoryResource
from cerid.resources.system import SystemResource
from cerid.resources.verify import VerifyResource


class CeridClient(_BaseClient):
    """Synchronous client for the Cerid AI SDK API.

    Usage::

        from cerid import CeridClient

        client = CeridClient(
            base_url="http://localhost:8888",
            client_id="my-app",
        )

        result = client.kb.query("How does chunking work?")
        print(result.context)

        health = client.system.health()
        print(health.status)
    """

    def __init__(
        self,
        base_url: str,
        client_id: str,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(base_url, client_id, api_key, timeout)
        self._http = httpx.Client(
            headers=self._build_headers(),
            timeout=self.timeout,
        )
        self._kb: Optional[KBResource] = None
        self._verify: Optional[VerifyResource] = None
        self._memory: Optional[MemoryResource] = None
        self._system: Optional[SystemResource] = None

    # -- Resource properties (lazy-initialized) --

    @property
    def kb(self) -> KBResource:
        """Knowledge-base operations: query, search, ingest, collections, taxonomy."""
        if self._kb is None:
            self._kb = KBResource(self, self._http)
        return self._kb

    @property
    def verify(self) -> VerifyResource:
        """Verification operations: hallucination detection."""
        if self._verify is None:
            self._verify = VerifyResource(self, self._http)
        return self._verify

    @property
    def memory(self) -> MemoryResource:
        """Memory operations: extraction and storage."""
        if self._memory is None:
            self._memory = MemoryResource(self, self._http)
        return self._memory

    @property
    def system(self) -> SystemResource:
        """System operations: health, settings, plugins."""
        if self._system is None:
            self._system = SystemResource(self, self._http)
        return self._system

    # -- Lifecycle --

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._http.close()

    def __enter__(self) -> CeridClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
