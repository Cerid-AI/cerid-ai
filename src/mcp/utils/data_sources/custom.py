# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Custom API data source — user-defined OpenAI-compatible or generic REST APIs."""
from __future__ import annotations

import logging
from typing import Any, Literal

import httpx

from utils.data_sources.base import DataSource, DataSourceResult

logger = logging.getLogger("ai-companion.data_sources.custom")


class CustomApiSource(DataSource):
    """User-configured external API data source.

    Supports three auth modes:
    - bearer: Authorization: Bearer <value>
    - custom_header: <auth_key>: <auth_value>
    - query_param: ?<auth_key>=<auth_value>
    """

    name: str = "custom"
    description: str = "User-defined API source"
    requires_api_key: bool = True

    def __init__(
        self,
        source_id: str,
        display_name: str,
        base_url: str,
        auth_type: Literal["bearer", "custom_header", "query_param"] = "bearer",
        auth_key: str = "Authorization",
        auth_value: str = "",
        response_path: str = "data",
        result_title_field: str = "title",
        result_content_field: str = "content",
        **kwargs: Any,
    ):
        self.source_id = source_id
        self.name = f"custom:{source_id}"
        self.display_name = display_name
        self.base_url = base_url.rstrip("/")
        self.auth_type = auth_type
        self.auth_key = auth_key
        self.auth_value = auth_value
        self.response_path = response_path
        self.result_title_field = result_title_field
        self.result_content_field = result_content_field

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.auth_type == "bearer":
            headers["Authorization"] = f"Bearer {self.auth_value}"
        elif self.auth_type == "custom_header":
            headers[self.auth_key] = self.auth_value
        return headers

    def _build_params(self, query: str) -> dict[str, str]:
        params = {"q": query}
        if self.auth_type == "query_param":
            params[self.auth_key] = self.auth_value
        return params

    def _extract_results(self, data: Any) -> list[dict]:
        """Walk the response_path (dot-separated) to find results array."""
        current = data
        for part in self.response_path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return []
        return current if isinstance(current, list) else [current]

    async def query(self, query: str, **kwargs: Any) -> list[DataSourceResult]:
        """Query the custom API and return parsed results."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.base_url}/search",
                    headers=self._build_headers(),
                    params=self._build_params(query),
                )
                resp.raise_for_status()
                data = resp.json()

            raw_results = self._extract_results(data)
            results = []
            for item in raw_results[:10]:
                title = item.get(self.result_title_field, "")
                content = item.get(self.result_content_field, "")
                if content:
                    results.append(DataSourceResult(
                        title=str(title),
                        content=str(content),
                        source_name=self.display_name,
                        source_url=self.base_url,
                    ))
            return results
        except (httpx.HTTPError, ValueError, KeyError) as e:
            logger.warning(f"Custom API query failed ({self.display_name}): {e}")
            return []

    async def test_connection(self) -> tuple[bool, str | None]:
        """Test connectivity to the custom API."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    self.base_url,
                    headers=self._build_headers(),
                )
                if resp.status_code < 500:
                    return True, None
                return False, f"Server returned {resp.status_code}"
        except (httpx.HTTPError, OSError) as e:
            return False, str(e)
