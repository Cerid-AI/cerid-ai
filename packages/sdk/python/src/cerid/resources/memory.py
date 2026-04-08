# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Memory resource: extraction and storage."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from cerid.errors import _raise_for_status
from cerid.models import MemoryExtractResponse

if TYPE_CHECKING:
    import httpx

    from cerid._base import _BaseClient


class MemoryResource:
    """Synchronous memory operations."""

    def __init__(self, client: _BaseClient, http: httpx.Client) -> None:
        self._client = client
        self._http = http

    def extract(
        self,
        text: str,
        *,
        conversation_id: Optional[str] = None,
    ) -> MemoryExtractResponse:
        """Extract facts, decisions, and preferences from text and store in KB.

        Args:
            text: Conversation text to extract memories from.
            conversation_id: Optional conversation identifier.
        """
        body = self._client._build_json(
            response_text=text,
            conversation_id=conversation_id,
        )
        resp = self._http.post(self._client._url("/memory/extract"), json=body)
        _raise_for_status(resp)
        return MemoryExtractResponse.model_validate(resp.json())


class AsyncMemoryResource:
    """Asynchronous memory operations."""

    def __init__(self, client: _BaseClient, http: httpx.AsyncClient) -> None:
        self._client = client
        self._http = http

    async def extract(
        self,
        text: str,
        *,
        conversation_id: Optional[str] = None,
    ) -> MemoryExtractResponse:
        """Extract facts, decisions, and preferences from text and store in KB."""
        body = self._client._build_json(
            response_text=text,
            conversation_id=conversation_id,
        )
        resp = await self._http.post(self._client._url("/memory/extract"), json=body)
        _raise_for_status(resp)
        return MemoryExtractResponse.model_validate(resp.json())
