# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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


_CATEGORY_ENUM = ["urgent", "actionable", "personal", "newsletter", "promo", "spam"]


@register_tool(
    name="pkb_inbox_triage",
    description=(
        "Trigger an AI inbox triage pass over recent unread Gmail, "
        "Outlook, and Apple Mail messages. Each thread is categorized "
        "(urgent / actionable / personal / newsletter / promo / spam) with a "
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
                "description": "Cap per source fetch. LLM cost scales linearly.",
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
        "**Use when** the user asks \"what's urgent\", \"personal mail "
        "this week\", \"actionable Outlook threads\". Only threads whose "
        "utility wrote a card are here: newsletter, promo, and spam "
        "threads are ledger-only unless financial. **Returns** "
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
                "enum": ["gmail", "outlook", "apple_mail", ""],
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


@register_tool(
    name="pkb_inbox_apply",
    description=(
        "Apply proposed inbox decisions. Dry-run is the default and "
        "does not change the mailbox. **Use when** the operator has "
        "approved rows in the review queue, or wants to see the plan "
        "first. Pass dry_run false only for an approved apply. "
        "**Returns** `{results, dry_run}`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "decision_ids": {
                "description": "Proposed decision ids. A list, or one comma-separated string.",
            },
            "dry_run": {
                "type": "boolean",
                "default": True,
                "description": "Plan the calls and do not perform them.",
            },
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "results": {"type": "array"},
            "dry_run": {"type": "boolean"},
            "status": {"type": "string"},
        },
    },
    cost_class="low",
)
async def pkb_inbox_apply(
    decision_ids: Any = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Apply or plan proposed decisions. The feature gate runs before the ledger opens."""
    from config.features import is_feature_enabled

    if not is_feature_enabled("inbox_triage"):
        return {"ok": False, "status": "feature_gated", "results": [], "dry_run": dry_run}
    from app.inbox.review import apply_ids, open_ledger

    return await apply_ids(decision_ids, dry_run=dry_run, ledger=open_ledger())


@register_tool(
    name="pkb_inbox_undo",
    description=(
        "Undo one applied inbox decision. Dry-run is the default and "
        "returns the plan without writing a new ledger row or touching "
        "the mailbox. **Use when** the operator wants a filed message "
        "returned to the mailbox it was in. **Returns** the apply "
        "result for that undo."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "decision_id": {"type": "string", "description": "Applied decision to undo."},
            "dry_run": {
                "type": "boolean",
                "default": True,
                "description": "Plan the undo and do not perform it.",
            },
        },
        "required": ["decision_id"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "status": {"type": "string"},
            "decision_id": {"type": "string"},
        },
    },
    cost_class="low",
)
async def pkb_inbox_undo(decision_id: str = "", dry_run: bool = True) -> dict[str, Any]:
    """Undo one applied decision. The feature gate runs before the ledger opens."""
    from config.features import is_feature_enabled

    if not is_feature_enabled("inbox_triage"):
        return {"ok": False, "status": "feature_gated", "decision_id": decision_id, "dry_run": dry_run}
    from app.inbox.review import open_ledger, undo_decision

    return await undo_decision(decision_id, dry_run=dry_run, ledger=open_ledger())


@register_tool(
    name="pkb_inbox_sender_pin",
    description=(
        "Pin a sender to one inbox action and category. Later triage "
        "passes skip the model for that sender. **Use when** the operator "
        "wants a correction to stick immediately, without waiting for "
        "three matching reversals. **Returns** `{ok, status, memory}`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "gmail, outlook, or apple_mail."},
            "sender": {"type": "string", "description": "The sender address."},
            "action": {"type": "string", "description": "keep, archive, mark_read, or draft."},
            "category": {
                "type": "string",
                "description": "urgent, actionable, personal, newsletter, promo, or spam.",
            },
        },
        "required": ["source", "sender", "action", "category"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "status": {"type": "string"},
            "memory": {"type": "object"},
            "reason": {"type": "string"},
        },
    },
    cost_class="low",
)
async def pkb_inbox_sender_pin(
    source: str = "",
    sender: str = "",
    action: str = "",
    category: str = "",
) -> dict[str, Any]:
    """Pin one sender. The feature gate runs before the ledger opens."""
    from config.features import is_feature_enabled

    if not is_feature_enabled("inbox_triage"):
        return {"ok": False, "status": "feature_gated"}
    from app.inbox.learn import pin_sender

    try:
        row = pin_sender(source=source, sender=sender, action=action, category=category)
    except ValueError as exc:
        return {"ok": False, "status": "invalid", "reason": str(exc)}
    return {"ok": True, "status": "pinned", "memory": row}


@register_tool(
    name="pkb_inbox_rule_upsert",
    description=(
        "Store an inbox rule. A matching rule skips the model on later "
        "passes. An exact provider beats a wildcard, and more match "
        "fields beat fewer. **Use when** the operator wants mail from a "
        "sender, domain, subject prefix, or list id filed the same way "
        "every time. **Returns** `{ok, status, rule}`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "rule_id": {
                "type": "string",
                "description": "Existing rule id. Empty creates a rule.",
            },
            "source": {
                "type": "string",
                "default": "*",
                "description": "gmail, outlook, apple_mail, or * for every provider.",
            },
            "condition": {
                "description": "Object or JSON string. Keys: from, domain, subject_prefix, list_id.",
            },
            "action": {"type": "string", "description": "keep, archive, mark_read, or draft."},
            "category": {
                "type": "string",
                "description": "urgent, actionable, personal, newsletter, promo, or spam.",
            },
            "enabled": {"type": "boolean", "default": True},
        },
        "required": ["condition", "action", "category"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "status": {"type": "string"},
            "rule": {"type": "object"},
            "reason": {"type": "string"},
        },
    },
    cost_class="low",
)
async def pkb_inbox_rule_upsert(
    rule_id: str = "",
    source: str = "*",
    condition: Any = None,
    action: str = "",
    category: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    """Store one rule. The feature gate runs before the ledger opens."""
    from config.features import is_feature_enabled

    if not is_feature_enabled("inbox_triage"):
        return {"ok": False, "status": "feature_gated"}
    from app.inbox.learn import upsert_rule

    try:
        row = upsert_rule(
            rule_id=rule_id,
            source=source,
            condition=condition,
            action=action,
            category=category,
            enabled=enabled,
        )
    except ValueError as exc:
        return {"ok": False, "status": "invalid", "reason": str(exc)}
    return {"ok": True, "status": "stored", "rule": row}


async def _list_inbox_artifacts(driver: Any, kwargs: dict[str, Any]) -> list[dict[str, Any]]:
    """Thin wrapper over graph.list_artifacts that flattens tag/meta
    properties into a single ``tags`` dict per row. Centralizing here so
    the filter+sort logic above stays declarative."""
    import asyncio

    from app.db import neo4j as graph_db
    rows = await asyncio.to_thread(graph_db.list_artifacts, driver, **kwargs)
    return rows or []
