# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared base client logic for sync and async variants."""

from __future__ import annotations

from typing import Any, Dict, Optional

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
        headers: Dict[str, str] = {
            "X-Client-ID": self.client_id,
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _url(self, path: str) -> str:
        """Build the full URL for an SDK endpoint path."""
        return f"{self.base_url}{SDK_PREFIX}{path}"

    def _build_json(self, **kwargs: Any) -> Dict[str, Any]:
        """Build a JSON body, dropping None values."""
        return {k: v for k, v in kwargs.items() if v is not None}
