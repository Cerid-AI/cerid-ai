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

import asyncio
import logging
import os
import re
from typing import Any

from app.data_sources.base import DataSource, DataSourceResult
from core.mcp_clients.result_text import tool_text
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.data_sources.gmail")

# Gmail search operators. If the user typed any of these they mean them
# literally, and rewriting the query would only break a precise request.
_GMAIL_OPERATOR_RE = re.compile(
    r"\b(from|to|cc|bcc|subject|label|in|is|has|filename|after|before|"
    r"older_than|newer_than|list|category|size|larger|smaller):",
    re.IGNORECASE,
)

# Words that describe the ASK rather than the mail. Searching message bodies
# for these is what produced "Found 0 messages" on every chat question.
_MAIL_META_WORDS = frozenset({
    "email", "emails", "mail", "message", "messages", "inbox", "gmail",
    "summarize", "summarise", "summary", "recent", "latest", "new", "unread",
    "find", "search", "show", "list", "get", "read", "check", "any", "anything",
    "my", "me", "i", "please", "tell", "about",
})

# What "my recent email" should actually retrieve. Bounded so a chat question
# never walks the whole mailbox.
_GMAIL_RECENCY_FALLBACK = "in:inbox newer_than:30d"

# Hydration budget, well inside the fan-out slice (3.0s for the first source,
# less afterwards). Search costs ~0.65s of that, so 1.5s leaves headroom.
_HYDRATE_BUDGET_S = float(os.getenv("GMAIL_HYDRATE_BUDGET_S", "1.5"))

# Sentinel for "we ran out of budget before attempting this one", kept distinct
# from None ("attempted and failed") so the two get different treatment.
_UNHYDRATED = object()


# Every google-workspace-mcp tool except ``start_google_auth`` declares
# ``user_google_email`` as a REQUIRED argument — verified against the live
# tools/list schema on 2026-08-27 (search_gmail_messages, get_events,
# get_gmail_message_content, list_calendars, ... all of them). Cerid passed it
# on none, so the sibling answered
#     1 validation error for call[...]
#     user_google_email  Missing required argument
# as a tool RESULT rather than an exception. client_pool logs that and returns
# the result, the parsers find no ids, and the connector reports "returned 0
# results" — indistinguishable from an empty mailbox. Same silent-zero shape as
# the 2026-08-09 ``max_results``/``q`` keyword defects, and it hid just as long.
#
# ``--single-user`` does NOT make the argument optional: it only fixes which
# account the OAuth flow consents. The value must still ride on every call.
def _google_account() -> str:
    """The Google account every sibling tool call is made on behalf of."""
    return os.getenv("USER_GOOGLE_EMAIL", "").strip()


class GmailDataSource(DataSource):
    name = "gmail"
    description = "Gmail messages via sibling google-workspace-mcp"
    requires_api_key = True
    api_key_env_var = "CERID_CONNECTORS_BEARER"  # pragma: allowlist secret

    def is_configured(self) -> bool:
        # Configured iff (a) bearer present, (b) Pro-tier gating allows it,
        # (c) the operator has actually wired the OAuth at the sibling MCP
        # server (we surface (c) via runtime call failure, not pre-flight).
        return (
            bool(os.getenv("CERID_CONNECTORS_BEARER"))
            and bool(os.getenv("GOOGLE_OAUTH_CLIENT_ID"))
            # Without an account the sibling rejects every tool call as a
            # validation error, which this class surfaces as zero results.
            # Report "not configured" instead of pretending an empty mailbox.
            and bool(_google_account())
        )

    async def _call_mcp(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Dispatch one tool call to the sibling google-workspace-mcp.

        Lazy-imports the pool so this module loads cleanly when the
        connector stack isn't running.
        """
        # Inject the account on every call rather than at each call site —
        # the argument is required by the whole tool surface, so a per-site
        # fix would just wait for the next tool to be added without it.
        account = _google_account()
        if account and "user_google_email" not in args:
            args = {**args, "user_google_email": account}

        from core.mcp_clients.client_pool import get_pool

        pool = get_pool()
        return await pool.call_tool("google_workspace", tool_name, args)

    def adapt_query(self, raw_query: str, keywords: list[str]) -> str:
        """Map a chat question onto Gmail search syntax.

        Gmail searches message CONTENT, so the inherited default — keywords
        joined with spaces — sends words that describe the *request* rather than
        the mail. A live call on 2026-08-27 went out as
        ``Query: 'summarize recent email'`` and the server answered
        ``Found 0 messages``: a correct search for a phrase nobody wrote. The
        connector looked broken when it was doing exactly what it was told.

        Three cases:
          - the user already typed operator syntax -> pass it through untouched
          - content terms survive the meta-word filter -> search those
          - nothing survives ("summarize my recent email") -> fall back to a
            RECENCY query instead of a nonsense content search, because the
            honest reading of that request is "my recent mail", not "mail
            containing the word summarize"
        """
        if _GMAIL_OPERATOR_RE.search(raw_query):
            return raw_query
        terms = [k for k in keywords if k.lower() not in _MAIL_META_WORDS]
        if not terms:
            return _GMAIL_RECENCY_FALLBACK
        return " ".join(terms[:6])

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
        # round-trip and we don't want every fan-out query to walk an inbox).
        #
        # CONCURRENTLY. These were sequential, which only became visible once
        # the search stopped returning nothing (2026-08-27): five round trips
        # in series blew the fan-out budget and the source started reporting
        # "gmail timed out after 3.0s" instead of delivering the mail it had
        # just found. The calls are independent reads of distinct message ids,
        # so the whole batch costs about one round trip. Failures stay
        # per-message — one unreadable message must not lose the others.
        async def _hydrate(mid: str) -> Any:
            try:
                return await self._call_mcp(
                    "get_gmail_message_content", {"message_id": mid},
                )
            except Exception as exc:  # noqa: BLE001 — sibling MCP can fail many ways
                log_swallowed_error("gmail.query.get_content", exc)
                return None

        # Bounded so a slow mailbox degrades instead of failing. Measured
        # 2026-08-27 against the live account: search 0.65s + five concurrent
        # hydrations 2.25s = 2.90s, against the 3.0s slice the fan-out hands
        # this source (less for sources later in the round). Sitting that close
        # to the cliff meant the whole call was cancelled and gmail contributed
        # NOTHING despite having already found the mail. Hydration now gets its
        # own budget; whatever does not land in time falls back to the
        # id-and-link citation below, so the source always returns its hits.
        try:
            details: list[Any] = await asyncio.wait_for(
                asyncio.gather(*(_hydrate(m["id"]) for m in messages[:max_full_fetch])),
                timeout=_HYDRATE_BUDGET_S,
            )
        except asyncio.TimeoutError:
            # Budget policy, NOT an error: the same class of decision as the
            # past-max_full_fetch path below, so it gets the same citation.
            logger.info(
                "gmail.hydrate budget %.1fs exceeded — returning %d citation-only result(s)",
                _HYDRATE_BUDGET_S, min(len(messages), max_full_fetch),
            )
            details = [_UNHYDRATED] * min(len(messages), max_full_fetch)

        def _citation_only(m: dict) -> DataSourceResult:
            """Cite a message we could not hydrate.

            The search reply carries no subject or snippet — only ids and links
            — so cite by id rather than inventing a title from a field that is
            not there.
            """
            return DataSourceResult(
                title=f"Gmail message {m['id']}",
                content="",
                source_url=m.get("web_link")
                or f"https://mail.google.com/mail/u/0/#all/{m['id']}",
                source_name="Gmail",
                confidence=0.55,
            )

        out: list[DataSourceResult] = []
        for i, msg in enumerate(messages):
            if i >= max_full_fetch:
                out.append(_citation_only(msg))
                continue
            detail = details[i]
            if detail is _UNHYDRATED:
                # Out of budget, never attempted. Cite it — a message we found
                # but never showed is the silent-zero failure this connector
                # spent its whole life in.
                out.append(_citation_only(msg))
                continue
            if detail is None:
                # Attempted and FAILED. Deliberately skipped, not cited: an
                # error is not budget policy, and test_query_skips_failed_
                # content_fetches pins that decision. Left as-is on purpose.
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
