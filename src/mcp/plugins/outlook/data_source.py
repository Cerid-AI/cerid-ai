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
import time
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

from app.data_sources.base import DataSource, DataSourceResult
from core.mcp_clients.result_text import is_error_result, tool_text
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.data_sources.outlook")

# A probe answer outlives the pool's in-memory flag, which a restart clears.
_PROBE_TTL_SECONDS = 600.0
_TRIAGE_WINDOW = timedelta(hours=24)
_TRIAGE_SELECT = ",".join((
    "id",
    "conversationId",
    "internetMessageId",
    "subject",
    "from",
    "toRecipients",
    "receivedDateTime",
    "bodyPreview",
    "isRead",
    "categories",
    "parentFolderId",
    "webLink",
    "internetMessageHeaders",
))


class OutlookDataSource(DataSource):
    name = "outlook"
    description = "Outlook Mail via sibling ms365-mcp"
    requires_api_key = True
    api_key_env_var = "CERID_CONNECTORS_BEARER"  # pragma: allowlist secret
    # Matches the connector name _call_mcp dispatches through (get_pool()
    # .call_tool("ms365", ...)) — lets query_all skip this source without
    # an MCP call while that connector's breaker is OPEN.
    mcp_connector_name = "ms365"
    # (configured, checked_at) from the last probe_configured call.
    _probed: ClassVar[tuple[bool, float] | None] = None

    @classmethod
    def forget_probe(cls) -> None:
        cls._probed = None

    @classmethod
    def _cached_probe(cls) -> bool | None:
        if cls._probed is None:
            return None
        configured, checked_at = cls._probed
        if time.monotonic() - checked_at > _PROBE_TTL_SECONDS:
            return None
        return configured

    def is_configured(self) -> bool:
        """True once the sibling has answered a call, or a recent probe did.

        This returned ``bool(CERID_CONNECTORS_BEARER)`` — a value the ms365
        sibling never reads as client auth. It forwards the client's bearer
        straight to Microsoft Graph, so our static token is not a credential
        here at all; the real credential is a device-code login cached inside
        the container's own volume, which this process cannot inspect.

        The result was `/connectors/outlook` reporting `configured` on an
        install that had never logged in — a green surface derived from
        evidence that had nothing to do with the thing being claimed.

        `ever_succeeded` is the only signal the backend genuinely has: it is
        set when a call returns a NON-ERROR result, so it cannot be satisfied
        by the 401s an unauthenticated sibling produces. It lives in memory,
        so a restart cleared it and a logged-in Outlook was skipped as
        not_configured until some other call happened to succeed. A probe
        answer within ``_PROBE_TTL_SECONDS`` fills that gap; the pool's own
        success still wins over a stale negative probe.
        """
        if self._pool_succeeded():
            return True
        return bool(self._cached_probe())

    def _pool_succeeded(self) -> bool:
        try:
            from core.mcp_clients.client_pool import get_pool

            for state in get_pool().list_connectors():
                if state.get("name") == "ms365":
                    return bool(state.get("ever_succeeded"))
        except Exception as exc:  # noqa: BLE001 — status must never 500
            log_swallowed_error("outlook.is_configured", exc)
        return False

    async def probe_configured(self) -> bool:
        """One cheap read against the sibling, cached for the TTL.

        Triage calls this before deciding the source is configured. A
        non-error answer is the same evidence the pool records; an error or
        a transport failure is cached as not configured so a down sibling is
        not re-probed on every call inside the window.
        """
        if self._pool_succeeded():
            type(self)._probed = (True, time.monotonic())
            return True
        cached = self._cached_probe()
        if cached is not None:
            return cached
        try:
            raw = await self._call_mcp("list-mail-folders", {"$top": 1})
            configured = not is_error_result(raw)
        except Exception as exc:  # noqa: BLE001 — a down sibling is "not configured", not a crash
            log_swallowed_error("outlook.probe_configured", exc)
            configured = False
        type(self)._probed = (configured, time.monotonic())
        return configured

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

    async def triage_query(self, query: str, **kwargs) -> list[DataSourceResult]:
        """The triage read: Inbox only, unread, received in the last day.

        ``query`` is Gmail syntax and is not sent. The folder-scoped tool
        cannot reach Junk Email or Deleted Items, and every row is stamped
        ``folder=inbox`` so a later pass can learn a cleared category.
        """
        del query
        max_results = int(kwargs.get("max_results", 30))
        since = (datetime.now(timezone.utc) - _TRIAGE_WINDOW).strftime("%Y-%m-%dT%H:%M:%SZ")
        args: dict[str, Any] = {
            "mailFolderId": "inbox",
            "$top": max_results,
            "$filter": f"isRead eq false and receivedDateTime ge {since}",
            "$select": _TRIAGE_SELECT,
        }
        try:
            raw = await self._call_mcp("list-mail-folder-messages", args)
        except Exception as exc:  # noqa: BLE001 — sibling MCP can fail many ways
            log_swallowed_error("outlook.triage_query", exc)
            return []
        return _to_results(parse_messages(raw), folder="inbox")

    async def apply(self, decision: dict, *, dry_run: bool = True, ledger: Any = None) -> dict:
        """Apply one Outlook decision. Dry-run does not call the sibling."""
        from app.inbox.executor import apply_decision
        from core.mcp_clients.result_text import is_error_result, tool_text

        class _Transport:
            async def call_tool(self, provider: str, tool: str, arguments: dict) -> str:
                raw = await self_source._call_mcp(tool, arguments)
                if is_error_result(raw):
                    raise RuntimeError(tool_text(raw)[:500] or tool)
                return tool_text(raw)

            async def apple(self, argv: list[str]) -> tuple[int, dict]:
                raise RuntimeError("outlook has no apple helper")

        self_source = self
        prepared = dict(decision)
        prepared.setdefault("provider", "outlook")
        return await apply_decision(prepared, dry_run=dry_run, ledger=ledger, transport=_Transport())


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


def _outlook_addresses(raw: object) -> str:
    """Comma-joined recipient addresses already present on the Graph row."""
    if not isinstance(raw, list):
        return ""
    addresses: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        email_address = item.get("emailAddress")
        if not isinstance(email_address, dict):
            continue
        address = str(email_address.get("address") or "").strip()
        if address:
            addresses.append(address)
    return ", ".join(addresses)


def _to_results(messages: list[dict[str, Any]], *, folder: str = "") -> list[DataSourceResult]:
    """``folder`` names the well-known folder the fetch was scoped to, if any."""
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
        message_id = str(m.get("id") or "")
        thread_id = str(m.get("conversationId") or "") or message_id
        metadata: dict[str, str] = {}
        if message_id:
            metadata["provider_message_id"] = message_id
        if thread_id:
            metadata["provider_thread_id"] = thread_id
        internet_id = str(m.get("internetMessageId") or "")
        if internet_id:
            metadata["message_id"] = internet_id
            metadata["rfc_message_id"] = internet_id
        categories = m.get("categories")
        if isinstance(categories, list) and categories:
            metadata["categories"] = ",".join(str(item) for item in categories if str(item).strip())
        elif isinstance(categories, str) and categories.strip():
            metadata["categories"] = categories.strip()
        raw_headers = m.get("internetMessageHeaders")
        if isinstance(raw_headers, list):
            header_map = {
                "list-id": "list_id",
                "list-unsubscribe": "list_unsubscribe",
                "authentication-results": "authentication_results",
                "x-spam-flag": "spam_flag",
                "x-spam-status": "spam_status",
                "message-id": "rfc_message_id",
                "to": "to",
                "date": "date",
                "in-reply-to": "in_reply_to",
                "references": "references",
            }
            for item in raw_headers:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip().casefold()
                value = str(item.get("value") or "").strip()
                dest = header_map.get(name)
                if not dest or not value:
                    continue
                if dest == "authentication_results" and dest in metadata:
                    # Every copy is kept so a sender's added "pass" cannot
                    # hide the provider's "fail" from the gate.
                    metadata[dest] = f"{metadata[dest]} ; {value}"
                elif dest not in metadata:
                    metadata[dest] = value
        if "to" not in metadata:
            addressed = _outlook_addresses(m.get("toRecipients"))
            if addressed:
                metadata["to"] = addressed
        if "date" not in metadata:
            received = str(m.get("receivedDateTime") or "").strip()
            if received:
                metadata["date"] = received
        parent = str(m.get("parentFolderId") or "").strip()
        if parent.casefold() in {"inbox", "archive", "drafts", "sentitems"}:
            metadata["folder"] = parent.casefold()
        elif folder:
            metadata["folder"] = folder
        out.append(
            DataSourceResult(
                title=subject,
                content=f"From: {from_addr}\nSubject: {subject}\n\n{body}",
                source_url=url,
                source_name="Outlook",
                confidence=0.7,
                metadata=metadata,
            ),
        )
    return out
