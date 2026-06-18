# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Gmail DataSource — Phase F Day 3.

Routes queries to the sibling google-workspace-mcp container via
MCPClientPool. The sibling server exposes:

  - search_gmail_messages(query, max_results) -> list[{id, snippet, ...}]
  - get_gmail_message_content(message_id) -> {subject, from, body, ...}

We map the user query to ``search_gmail_messages``, then optionally
fetch full content for the top N results (controlled by
``GMAIL_MAX_FULL_FETCH``, default 5). Each result becomes a
DataSourceResult that the query agent's fan-out merges with KB +
other sources.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from app.data_sources.base import DataSource, DataSourceResult
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.data_sources.gmail")


class GmailDataSource(DataSource):
    name = "gmail"
    description = "Gmail messages via sibling google-workspace-mcp"
    requires_api_key = True
    api_key_env_var = "CERID_CONNECTORS_BEARER"  # pragma: allowlist secret

    def is_configured(self) -> bool:
        # Configured iff (a) bearer present, (b) Pro-tier gating allows it,
        # (c) the operator has actually wired the OAuth at the sibling MCP
        # server (we surface (c) via runtime call failure, not pre-flight).
        return bool(os.getenv("CERID_CONNECTORS_BEARER")) and bool(
            os.getenv("GOOGLE_OAUTH_CLIENT_ID")
        )

    async def _call_mcp(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Dispatch one tool call to the sibling google-workspace-mcp.

        Lazy-imports the pool so this module loads cleanly when the
        connector stack isn't running.
        """
        from core.mcp_clients.client_pool import get_pool

        pool = get_pool()
        return await pool.call_tool("google_workspace", tool_name, args)

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        max_results = int(kwargs.get("max_results", 10))
        max_full_fetch = int(os.getenv("GMAIL_MAX_FULL_FETCH", "5"))
        try:
            search = await self._call_mcp(
                "search_gmail_messages",
                {"query": query, "max_results": max_results},
            )
        except Exception as exc:  # noqa: BLE001 — sibling MCP can fail many ways
            log_swallowed_error("gmail.query.search", exc)
            return []

        messages = _coerce_message_list(search)
        if not messages:
            return []

        # Hydrate the top N with full body (bounded — full-fetch is per-message
        # round-trip and we don't want every fan-out query to walk an inbox)
        out: list[DataSourceResult] = []
        for i, msg in enumerate(messages):
            if i >= max_full_fetch:
                # Past the budget — return snippet-only DataSourceResult so the
                # answer still cites it without paying the round-trip.
                out.append(
                    DataSourceResult(
                        title=msg.get("subject") or msg.get("snippet", "")[:80] or "(no subject)",
                        content=msg.get("snippet", ""),
                        source_url=f"https://mail.google.com/mail/u/0/#inbox/{msg.get('id', '')}",
                        source_name="Gmail",
                        confidence=0.55,
                    ),
                )
                continue
            try:
                detail = await self._call_mcp(
                    "get_gmail_message_content",
                    {"message_id": msg.get("id", "")},
                )
            except Exception as exc:  # noqa: BLE001
                log_swallowed_error("gmail.query.get_content", exc)
                continue
            detail_dict = _coerce_message_detail(detail)
            if not detail_dict:
                continue
            out.append(
                DataSourceResult(
                    title=detail_dict.get("subject", "(no subject)"),
                    content=_compose_body(detail_dict),
                    source_url=f"https://mail.google.com/mail/u/0/#inbox/{msg.get('id', '')}",
                    source_name="Gmail",
                    confidence=0.75,
                ),
            )
        return out


def _coerce_message_list(raw: Any) -> list[dict[str, Any]]:
    """Tools/list shape varies across MCP server versions. Normalize to a
    list of dicts."""
    if isinstance(raw, list):
        return [m for m in raw if isinstance(m, dict)]
    if isinstance(raw, dict):
        for key in ("messages", "results", "data"):
            if isinstance(raw.get(key), list):
                return [m for m in raw[key] if isinstance(m, dict)]
    return []


def _coerce_message_detail(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw[0]
    return None


def _compose_body(detail: dict[str, Any]) -> str:
    from_addr = detail.get("from") or detail.get("sender") or "(unknown)"
    subject = detail.get("subject", "(no subject)")
    body = detail.get("body") or detail.get("snippet") or detail.get("plain_text") or ""
    return f"From: {from_addr}\nSubject: {subject}\n\n{body}"
