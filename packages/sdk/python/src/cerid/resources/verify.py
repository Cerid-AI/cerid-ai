# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Verification resource: hallucination detection."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from cerid.errors import _raise_for_status
from cerid.models import HallucinationResponse

if TYPE_CHECKING:
    import httpx

    from cerid._base import _BaseClient


class VerifyResource:
    """Synchronous verification operations."""

    def __init__(self, client: _BaseClient, http: httpx.Client) -> None:
        self._client = client
        self._http = http

    def check(
        self,
        response: str,
        *,
        context: str = "",
        conversation_id: Optional[str] = None,
        claims: Optional[List[str]] = None,
    ) -> HallucinationResponse:
        """Verify factual claims in a response against the KB.

        Args:
            response: The LLM response text to verify.
            context: Original context/query used to generate the response.
            conversation_id: Optional conversation identifier.
            claims: Pre-extracted claims to verify (skips extraction step).
        """
        body = self._client._build_json(
            response_text=response,
            conversation_id=conversation_id,
            claims=claims,
        )
        resp = self._http.post(self._client._url("/hallucination"), json=body)
        _raise_for_status(resp)
        return HallucinationResponse.model_validate(resp.json())


class AsyncVerifyResource:
    """Asynchronous verification operations."""

    def __init__(self, client: _BaseClient, http: httpx.AsyncClient) -> None:
        self._client = client
        self._http = http

    async def check(
        self,
        response: str,
        *,
        context: str = "",
        conversation_id: Optional[str] = None,
        claims: Optional[List[str]] = None,
    ) -> HallucinationResponse:
        """Verify factual claims in a response against the KB."""
        body = self._client._build_json(
            response_text=response,
            conversation_id=conversation_id,
            claims=claims,
        )
        resp = await self._http.post(self._client._url("/hallucination"), json=body)
        _raise_for_status(resp)
        return HallucinationResponse.model_validate(resp.json())
