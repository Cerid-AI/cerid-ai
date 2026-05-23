# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Inbox triage MCP tools — Phase J Day 3.

Two tools:

* ``pkb_inbox_triage`` — kicks off a fresh triage pass and returns
  the categorized thread list. Used by the Sources → Connectors
  panel's manual "Run now" button.

* ``pkb_inbox_filter`` — read-only query against previously-triaged
  threads stored as artifacts in the ``inbox`` domain. Used by chat
  questions like "what's urgent today" — no LLM call needed since
  triage already ran.

Both are Pro-tier gated via ``inbox_triage`` feature flag; the
``register_tool`` machinery surfaces a clean upgrade CTA in the chat
when a free-tier user invokes them.
"""
from __future__ import annotations

import logging
from typing import Any

from app.tool_registry import register_tool

logger = logging.getLogger("ai-companion.mcp_tools.inbox")


_CATEGORY_ENUM = ["urgent", "actionable", "personal", "newsletter", "promo"]


@register_tool(
    name="pkb_inbox_triage",
    description=(
        "Trigger an AI inbox triage pass over recent unread Gmail + "
        "Outlook messages. Each thread is categorized "
        "(urgent / actionable / personal / newsletter / promo) with a "
        "one-sentence summary and a suggested action, then persisted "
        "to the KB in domain='inbox'. **Use when** the user asks for a "
        "fresh categorized inbox view (\"what came in today?\", "
        "\"triage my inbox\"). For \"what's urgent\" style questions "
        "against an already-triaged inbox, use pkb_inbox_filter "
        "instead — it's a no-LLM read. **Returns** `{threads: "
        "[{thread_id, source, category, summary, suggested_action, "
        "participants, subject, message_count, latest_at, artifact_id}], "
        "by_category, sources_queried, skipped}`. Pro-tier; community "
        "users see an upgrade CTA."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Source-specific filter (Gmail honors operators like 'is:unread newer_than:1d').",
                "default": "is:unread newer_than:1d",
            },
            "max_results_per_source": {
                "type": "integer",
                "default": 30,
                "description": "Cap per Gmail/Outlook fetch. LLM cost scales linearly.",
            },
            "persist": {
                "type": "boolean",
                "default": True,
                "description": "Write triaged threads back to KB. Set False for dry-run.",
            },
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "threads": {"type": "array"},
            "by_category": {"type": "object"},
            "sources_queried": {"type": "array", "items": {"type": "string"}},
            "skipped": {"type": "array"},
        },
    },
    cost_class="high",  # LLM-per-thread; tag for budget tracking
)
async def pkb_inbox_triage(
    query: str = "is:unread newer_than:1d",
    max_results_per_source: int = 30,
    persist: bool = True,
) -> dict[str, Any]:
    """Run inbox_triage on demand. Feature gate enforced inside the agent."""
    from core.agents.inbox_triage import triage_inboxes

    result = await triage_inboxes(
        query=query,
        max_results_per_source=max_results_per_source,
        persist=persist,
    )
    return result.to_dict()


@register_tool(
    name="pkb_inbox_filter",
    description=(
        "Query previously-triaged inbox threads by category, source, "
        "or recency. Read-only (no LLM call). Pulls from KB artifacts "
        "in domain='inbox' that were written by pkb_inbox_triage. "
        "**Use when** the user asks \"what's urgent\", \"newsletters "
        "from this week\", \"actionable Outlook threads\". **Returns** "
        "`{threads: [{thread_id, subject, category, summary, "
        "suggested_action, source, artifact_id}], total, "
        "filter_applied}`. Pro-tier."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": _CATEGORY_ENUM,
                "description": "Optional category filter.",
            },
            "source": {
                "type": "string",
                "enum": ["gmail", "outlook", ""],
                "default": "",
                "description": "Optional origin filter.",
            },
            "since_days": {
                "type": "integer",
                "default": 7,
                "description": "Recency window in days.",
            },
            "max_results": {
                "type": "integer",
                "default": 50,
            },
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "threads": {"type": "array"},
            "total": {"type": "integer"},
            "filter_applied": {"type": "object"},
        },
    },
    cost_class="low",  # Pure KB read
)
async def pkb_inbox_filter(
    category: str | None = None,
    source: str = "",
    since_days: int = 7,
    max_results: int = 50,
) -> dict[str, Any]:
    """Filter triaged threads by category/source/recency."""
    from config.features import is_feature_enabled

    if not is_feature_enabled("inbox_triage"):
        return {
            "threads": [],
            "total": 0,
            "filter_applied": {"feature_gated": True},
        }

    # Pull from the inbox domain via the existing artifact query path.
    # Each triaged thread lives as a single artifact with rich metadata.
    try:
        from app.deps import get_neo4j
        driver = get_neo4j()
    except ImportError:
        return {"threads": [], "total": 0, "filter_applied": {"error": "neo4j unavailable"}}

    from datetime import datetime, timedelta, timezone
    since = (datetime.now(tz=timezone.utc) - timedelta(days=since_days)).isoformat()

    list_kwargs: dict[str, Any] = {
        "domain": "inbox",
        "limit": max_results,
        "since": since,
    }
    try:
        artifacts = await _list_inbox_artifacts(driver, list_kwargs)
    except Exception as exc:  # noqa: BLE001 — defensive, never crash chat
        logger.warning("pkb_inbox_filter list failed: %s", exc)
        return {
            "threads": [],
            "total": 0,
            "filter_applied": {"error": str(exc)},
        }

    # Apply category + source filters in-memory (small N, post-fetch).
    filtered = []
    for art in artifacts:
        tags = art.get("tags", {}) or {}
        if category and tags.get("category") != category:
            continue
        if source and tags.get("origin_source") != source:
            continue
        filtered.append({
            "thread_id": tags.get("thread_id", ""),
            "subject": tags.get("subject") or art.get("filename", ""),
            "category": tags.get("category", "unknown"),
            "summary": tags.get("summary", ""),
            "suggested_action": tags.get("suggested_action", ""),
            "source": tags.get("origin_source", "unknown"),
            "artifact_id": art.get("id"),
        })

    return {
        "threads": filtered,
        "total": len(filtered),
        "filter_applied": {
            "category": category or "",
            "source": source,
            "since_days": since_days,
        },
    }


async def _list_inbox_artifacts(driver: Any, kwargs: dict[str, Any]) -> list[dict[str, Any]]:
    """Thin wrapper over graph.list_artifacts that flattens tag/meta
    properties into a single ``tags`` dict per row. Centralizing here so
    the filter+sort logic above stays declarative."""
    import asyncio

    from app.db import neo4j as graph_db
    rows = await asyncio.to_thread(graph_db.list_artifacts, driver, **kwargs)
    return rows or []
