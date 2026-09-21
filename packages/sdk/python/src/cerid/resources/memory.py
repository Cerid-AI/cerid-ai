# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Memory resource: extraction and storage."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Union

from cerid.errors import _raise_for_status
from cerid.models import (
    MemoryExtractAcceptedResponse,
    MemoryExtractJobStatus,
    MemoryExtractResponse,
    MemoryRecallResponse,
)

ExtractResult = Union[MemoryExtractResponse, MemoryExtractAcceptedResponse]

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
        conversation_id: str,
        timeout: Optional[float] = None,
    ) -> ExtractResult:
        """Extract facts, decisions, and preferences from text and store in KB.

        Returns a :class:`~cerid.models.MemoryExtractResponse` when the server
        ran the extraction inline (HTTP 200), or a
        :class:`~cerid.models.MemoryExtractAcceptedResponse` when it enqueued
        the work (HTTP 202, the default on servers with
        ``MEMORY_QUEUE_MODE=async``). Branch on the type — the accepted
        envelope carries the ``job_id`` to hand to :meth:`get_job`::

            result = client.memory.extract("...", conversation_id="conv-1")
            if isinstance(result, MemoryExtractAcceptedResponse):
                status = client.memory.get_job(result.job_id)

        Args:
            text: Conversation text to extract memories from.
            conversation_id: Conversation identifier the memories are filed
                under. Required by the server (``min_length=1``).
        """
        body = self._client._build_json(
            response_text=text,
            conversation_id=conversation_id,
        )
        resp = self._http.post(
            self._client._url("/memory/extract"),
            json=body,
            headers=self._client._write_headers(),
            timeout=self._client._http_timeout(timeout),
        )
        _raise_for_status(resp)
        if resp.status_code == 202:
            return MemoryExtractAcceptedResponse.model_validate(resp.json())
        return MemoryExtractResponse.model_validate(resp.json())

    def recall(
        self,
        query: str,
        *,
        top_k: int = 5,
        min_score: float = 0.4,
        timeout: Optional[float] = None,
    ) -> MemoryRecallResponse:
        """Salience-aware memory recall."""
        body = self._client._build_json(query=query, top_k=top_k, min_score=min_score)
        resp = self._http.post(
            self._client._url("/memory/recall"),
            json=body,
            timeout=self._client._http_timeout(timeout),
        )
        _raise_for_status(resp)
        return MemoryRecallResponse.model_validate(resp.json())

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
        conversation_id: str,
        timeout: Optional[float] = None,
    ) -> ExtractResult:
        """Extract memories; 202 yields a
        :class:`~cerid.models.MemoryExtractAcceptedResponse` to poll with
        :meth:`get_job` — see :meth:`MemoryResource.extract`."""
        body = self._client._build_json(
            response_text=text,
            conversation_id=conversation_id,
        )
        resp = await self._http.post(
            self._client._url("/memory/extract"),
            json=body,
            headers=self._client._write_headers(),
            timeout=self._client._http_timeout(timeout),
        )
        _raise_for_status(resp)
        if resp.status_code == 202:
            return MemoryExtractAcceptedResponse.model_validate(resp.json())
        return MemoryExtractResponse.model_validate(resp.json())

    async def recall(
        self,
        query: str,
        *,
        top_k: int = 5,
        min_score: float = 0.4,
        timeout: Optional[float] = None,
    ) -> MemoryRecallResponse:
        """Salience-aware memory recall."""
        body = self._client._build_json(query=query, top_k=top_k, min_score=min_score)
        resp = await self._http.post(
            self._client._url("/memory/recall"),
            json=body,
            timeout=self._client._http_timeout(timeout),
        )
        _raise_for_status(resp)
        return MemoryRecallResponse.model_validate(resp.json())

    async def get_job(self, job_id: str) -> MemoryExtractJobStatus:
        """Async variant of :meth:`MemoryResource.get_job` — poll an async
        memory_extract job by ``job_id``."""
        resp = await self._http.get(
            self._client._url(f"/memory/extract/jobs/{job_id}"),
        )
        _raise_for_status(resp)
        return MemoryExtractJobStatus.model_validate(resp.json())
