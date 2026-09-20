# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared base client logic for sync and async variants."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from cerid.__version__ import SDK_PROTOCOL_VERSION
from cerid.errors import ProtocolVersionError

DEFAULT_TIMEOUT = 30.0
SDK_PREFIX = "/sdk/v1"


class _BaseClient:
    """Configuration and header logic shared by both client variants."""

    def __init__(
        self,
        base_url: str,
        client_id: str,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.api_key = api_key
        self.timeout = timeout

    def _build_headers(self) -> Dict[str, str]:
        # Do not default Content-Type: application/json. json= posts let httpx
        # set it; multipart ingest_bytes needs a boundary Content-Type instead.
        # httpx 0.28 also rejects Content-Type: None as an unset.
        headers: Dict[str, str] = {
            "X-Client-ID": self.client_id,
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _url(self, path: str) -> str:
        """Build the full URL for an SDK endpoint path."""
        return f"{self.base_url}{SDK_PREFIX}{path}"

    def _assert_protocol_compatible(self, server_version: object) -> None:
        """Compare the server's stated wire protocol against this client's pin.

        Called from the responses that carry the server's version (health,
        health/detailed, settings) — the only place it is stated — so the check
        costs no extra round trip. A server that states no usable version is
        unknown, not incompatible.
        """
        if not isinstance(server_version, str) or not server_version:
            return
        if server_version.split(".")[0] == SDK_PROTOCOL_VERSION.split(".")[0]:
            return
        raise ProtocolVersionError(
            f"Server wire protocol {server_version} is incompatible with this "
            f"SDK, which was built against {SDK_PROTOCOL_VERSION}. Upgrade "
            "cerid-sdk to a release that targets the server's major version.",
            server_version,
        )

    def _build_json(self, **kwargs: Any) -> Dict[str, Any]:
        """Build a JSON body, dropping None values."""
        return {k: v for k, v in kwargs.items() if v is not None}

    def _http_timeout(self, timeout: Optional[float] = None) -> float:
        return self.timeout if timeout is None else timeout

    def _write_headers(self, idempotency_key: Optional[str] = None) -> Dict[str, str]:
        return {"Idempotency-Key": idempotency_key or str(uuid.uuid4())}
