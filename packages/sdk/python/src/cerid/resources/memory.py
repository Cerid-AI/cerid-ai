# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Memory resource: extraction and storage."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from cerid.errors import _raise_for_status
from cerid.models import MemoryExtractJobStatus, MemoryExtractResponse

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
            text=text,
            conversation_id=conversation_id,
        )
        resp = self._http.post(self._client._url("/memory/extract"), json=body)
        _raise_for_status(resp)
        return MemoryExtractResponse.model_validate(resp.json())

    def get_job(self, job_id: str) -> MemoryExtractJobStatus:
        """Poll an async memory_extract job by its ``job_id``.

        When the server is configured with ``MEMORY_QUEUE_MODE=async``,
        ``extract`` may return a 202 Accepted envelope with a ``job_id``;
        callers use this method to poll for completion. Status transitions
        ``queued → started → finished | failed``. The ``result`` field is
        populated only on ``finished``; ``error`` only on ``failed``.
        """
        resp = self._http.get(
            self._client._url(f"/memory/extract/jobs/{job_id}"),
        )
        _raise_for_status(resp)
        return MemoryExtractJobStatus.model_validate(resp.json())


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
            text=text,
            conversation_id=conversation_id,
        )
        resp = await self._http.post(self._client._url("/memory/extract"), json=body)
        _raise_for_status(resp)
        return MemoryExtractResponse.model_validate(resp.json())

    async def get_job(self, job_id: str) -> MemoryExtractJobStatus:
        """Async variant of :meth:`MemoryResource.get_job` — poll an async
        memory_extract job by ``job_id``."""
        resp = await self._http.get(
            self._client._url(f"/memory/extract/jobs/{job_id}"),
        )
        _raise_for_status(resp)
        return MemoryExtractJobStatus.model_validate(resp.json())
