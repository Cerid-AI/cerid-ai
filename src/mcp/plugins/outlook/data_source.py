# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""Outlook Mail DataSource — Phase F Day 6.

Mirrors GmailDataSource's shape, routed to the sibling ms365-mcp container.

The tool is ``list-mail-messages`` (``GET /me/messages``, scope ``Mail.Read``),
read from the running image's own ``dist/endpoints.json`` on 2026-08-10. It
takes OData parameters only — ``$search``, ``$top``, ``$filter``, … — and
``$search`` values must be quoted KQL, which the server's own tool description
calls out as CRITICAL.

Two corrections worth keeping, because the previous version was written from
assumption rather than from the catalogue:

* It called ``search-messages`` / ``search_messages`` / ``list-messages``.
  None of the three exist. The server answers an unknown tool with a normal
  result carrying ``isError``, not an exception, so the ``except`` never fired,
  all three "succeeded", and every Outlook query returned zero results for the
  connector's entire life — reported as an empty mailbox.
* Unknown argument names are not rejected either: the schema is
  ``.passthrough()``, so the server logs "Dropping unrecognized parameter" and
  continues. A wrong name is silent.

Unlike the Google sibling (which answers in prose), ms365 returns real JSON as
text, so the reply is ``json.loads(tool_text(raw))``.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from app.data_sources.base import DataSource, DataSourceResult
from core.mcp_clients.result_text import is_error_result, tool_text
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
        args: dict[str, Any] = {"$top": max_results}
        if query:
            # KQL, and the quotes are required by Graph — an unquoted value is
            # rejected upstream, not here.
            args["$search"] = f'"{query}"'
        try:
            raw = await self._call_mcp("list-mail-messages", args)
        except Exception as exc:  # noqa: BLE001 — sibling MCP can fail many ways
            log_swallowed_error("outlook.query", exc)
            return []
        return _to_results(parse_messages(raw))


def parse_messages(raw: Any) -> list[dict[str, Any]]:
    """Graph message dicts from an ms365 tool result.

    The old coercer isinstance-checked for ``list``/``dict``. ``call_tool``
    returns a ``CallToolResult``, so it matched neither and silently returned
    ``[]`` — which reads as an empty mailbox rather than as a fault.
    """
    if is_error_result(raw):
        # An error result is not an empty mailbox. Say so, or this failure
        # stays indistinguishable from "no mail matched".
        logger.warning("outlook: tool returned an error result: %s", tool_text(raw)[:200])
        return []
    text = tool_text(raw)
    if not text:
        return []
    try:
        payload = json.loads(text)
    except ValueError as exc:
        log_swallowed_error("outlook.parse_messages", exc)
        return []
    if isinstance(payload, list):
        return [m for m in payload if isinstance(m, dict)]
    if isinstance(payload, dict):
        value = payload.get("value")
        if isinstance(value, list):
            return [m for m in value if isinstance(m, dict)]
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
