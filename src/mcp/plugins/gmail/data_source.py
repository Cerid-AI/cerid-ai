# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""Gmail DataSource — Phase F Day 3.

Routes queries to the sibling google-workspace-mcp container via
MCPClientPool. The sibling server exposes:

  - search_gmail_messages(query, page_size, ...) -> PROSE listing message ids
  - get_gmail_message_content(message_id, ...)   -> PROSE headers + body

**Both return human-readable text, not JSON.** This module was originally
written against a structured API that the server has never had — it declared
``-> list[{id, snippet, ...}]`` and type-checked for a list or dict. A
``CallToolResult`` is neither, so every query silently produced zero results
and looked exactly like an empty mailbox. Verified against the live tool
schemas on 2026-08-09; ``structuredContent`` is only ``{"result": <same
prose>}``, so parsing is the only route. See ``parse_message_ids`` /
``parse_message_detail``, whose tests pin the formats.

We map the user query to ``search_gmail_messages``, then optionally
fetch full content for the top N results (controlled by
``GMAIL_MAX_FULL_FETCH``, default 5). Each result becomes a
DataSourceResult that the query agent's fan-out merges with KB +
other sources.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from app.data_sources.base import DataSource, DataSourceResult
from core.mcp_clients.result_text import tool_text
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
            # `page_size`, not `max_results`. The sibling validates arguments
            # with pydantic and rejects unknown keywords outright:
            #   "1 validation error … max_results Unexpected keyword argument".
            # That error came back as a tool RESULT rather than an exception,
            # so the except below never fired, _coerce_message_list found
            # nothing, and the connector logged "returned 0 results" on every
            # query — indistinguishable from an empty mailbox. Verified against
            # the live tool schema on 2026-08-09.
            search = await self._call_mcp(
                "search_gmail_messages",
                {"query": query, "page_size": max_results},
            )
        except Exception as exc:  # noqa: BLE001 — sibling MCP can fail many ways
            log_swallowed_error("gmail.query.search", exc)
            return []

        messages = parse_message_ids(search)
        if not messages:
            return []

        # Hydrate the top N with full body (bounded — full-fetch is per-message
        # round-trip and we don't want every fan-out query to walk an inbox)
        out: list[DataSourceResult] = []
        for i, msg in enumerate(messages):
            if i >= max_full_fetch:
                # Past the budget. The search reply carries no subject or
                # snippet — only ids and links — so cite the message by id
                # rather than inventing a title from a field that is not there.
                out.append(
                    DataSourceResult(
                        title=f"Gmail message {msg['id']}",
                        content="",
                        source_url=msg.get("web_link")
                        or f"https://mail.google.com/mail/u/0/#all/{msg['id']}",
                        source_name="Gmail",
                        confidence=0.55,
                    ),
                )
                continue
            try:
                detail = await self._call_mcp(
                    "get_gmail_message_content",
                    {"message_id": msg["id"]},
                )
            except Exception as exc:  # noqa: BLE001
                log_swallowed_error("gmail.query.get_content", exc)
                continue
            detail_dict = parse_message_detail(detail)
            if not detail_dict:
                continue
            out.append(
                DataSourceResult(
                    title=detail_dict.get("subject") or "(no subject)",
                    content=_compose_body(detail_dict),
                    source_url=msg.get("web_link")
                    or f"https://mail.google.com/mail/u/0/#all/{msg['id']}",
                    source_name="Gmail",
                    confidence=0.75,
                ),
            )
        return out


# `search_gmail_messages` answers in prose, one indented block per hit:
#     1. Message ID: 19fe93167f7e153f
#        Web Link: https://mail.google.com/mail/u/0/#all/19fe93167f7e153f
# There is no structured alternative — structuredContent carries the same
# string — so the id is extracted by pattern. Anchored on the label so an
# id appearing in a subject line cannot be mistaken for a result.
_MESSAGE_ID_RE = re.compile(r"^\s*\d+\.\s*Message ID:\s*(\S+)", re.MULTILINE)
_WEB_LINK_RE = re.compile(r"^\s*Web Link:\s*(\S+)", re.MULTILINE)

# `get_gmail_message_content` answers with RFC822-ish headers, a `--- BODY ---`
# separator, then the body.
_BODY_SEPARATOR = "--- BODY ---"
_HEADER_RE = re.compile(r"^(Subject|From|Date|To):\s*(.*)$", re.MULTILINE)


def parse_message_ids(raw: Any) -> list[dict[str, str]]:
    """Message ids (and web links, positionally) from a search reply."""
    text = tool_text(raw)
    ids = _MESSAGE_ID_RE.findall(text)
    links = _WEB_LINK_RE.findall(text)
    return [
        {"id": mid, "web_link": links[i] if i < len(links) else ""}
        for i, mid in enumerate(ids)
    ]


def parse_message_detail(raw: Any) -> dict[str, str]:
    """Headers + body from a message-content reply. Empty dict when unparseable."""
    text = tool_text(raw)
    if not text:
        return {}
    head, _, body = text.partition(_BODY_SEPARATOR)
    detail = {k.lower(): v.strip() for k, v in _HEADER_RE.findall(head)}
    if body:
        detail["body"] = body.strip()
    return detail


def _compose_body(detail: dict[str, Any]) -> str:
    from_addr = detail.get("from") or detail.get("sender") or "(unknown)"
    subject = detail.get("subject", "(no subject)")
    body = detail.get("body") or detail.get("snippet") or detail.get("plain_text") or ""
    return f"From: {from_addr}\nSubject: {subject}\n\n{body}"
