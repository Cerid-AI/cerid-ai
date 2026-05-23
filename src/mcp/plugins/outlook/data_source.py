# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Outlook Mail DataSource — Phase F Day 6.

Mirrors GmailDataSource's shape, routed to the sibling ms365-mcp
container. The Microsoft Graph API exposes message search via
``search-mail`` (Softeria tool naming varies — coerce defensively).
"""
from __future__ import annotations

import logging
import os
from typing import Any

from app.data_sources.base import DataSource, DataSourceResult
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.data_sources.outlook")


class OutlookDataSource(DataSource):
    name = "outlook"
    description = "Outlook Mail via sibling ms365-mcp"
    requires_api_key = True
    api_key_env_var = "CERID_CONNECTORS_BEARER"  # pragma: allowlist secret

    def is_configured(self) -> bool:
        return bool(os.getenv("CERID_CONNECTORS_BEARER"))

    async def _call_mcp(self, tool_name: str, args: dict[str, Any]) -> Any:
        from core.mcp_clients.client_pool import get_pool

        pool = get_pool()
        return await pool.call_tool("ms365", tool_name, args)

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        max_results = int(kwargs.get("max_results", 10))
        # Softeria's tool names follow Microsoft Graph conventions; the
        # canonical mail-search tool is ``search-messages``. Some pinned
        # SHAs use ``list-messages`` with a $search OData param. Try
        # both — most operators won't care which one wired.
        for tool_name in ("search-messages", "search_messages", "list-messages"):
            try:
                raw = await self._call_mcp(
                    tool_name,
                    {"query": query, "limit": max_results},
                )
                messages = _coerce_messages(raw)
                if messages:
                    return _to_results(messages)
            except Exception as exc:  # noqa: BLE001
                log_swallowed_error(f"outlook.query.{tool_name}", exc)
                continue
        return []


def _coerce_messages(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [m for m in raw if isinstance(m, dict)]
    if isinstance(raw, dict):
        for key in ("value", "messages", "results", "data"):
            if isinstance(raw.get(key), list):
                return [m for m in raw[key] if isinstance(m, dict)]
    return []


def _to_results(messages: list[dict[str, Any]]) -> list[DataSourceResult]:
    out: list[DataSourceResult] = []
    for m in messages:
        # Microsoft Graph fields: subject, from.{emailAddress.address},
        # body.content, bodyPreview, webLink
        from_field = m.get("from", {})
        from_addr = (
            from_field.get("emailAddress", {}).get("address")
            if isinstance(from_field, dict)
            else from_field
        ) or "(unknown)"
        subject = m.get("subject") or "(no subject)"
        body = (
            (m.get("body") or {}).get("content")
            if isinstance(m.get("body"), dict)
            else m.get("body")
        ) or m.get("bodyPreview") or ""
        url = m.get("webLink") or "https://outlook.live.com/mail/0/"
        out.append(
            DataSourceResult(
                title=subject,
                content=f"From: {from_addr}\nSubject: {subject}\n\n{body}",
                source_url=url,
                source_name="Outlook",
                confidence=0.7,
            ),
        )
    return out
