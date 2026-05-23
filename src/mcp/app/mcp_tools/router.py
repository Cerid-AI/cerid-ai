# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase K3.4 — surface-router MCP tool.

Exposes the surface router (Phase K3.1 / ``core/retrieval/surface_router.py``)
as a first-class MCP tool so agents can explicitly ask "which surfaces
should I consult for this query" without each agent re-implementing
the routing heuristics.

Useful for orchestration patterns like the Boardroom agent or custom
templates that pre-select retrieval surfaces based on user intent.
"""
from __future__ import annotations

import logging
from typing import Any

from app.tool_registry import InvalidParamsError, register_tool
from core.retrieval.surface_router import route as _route

logger = logging.getLogger("ai-companion.mcp_tools.router")


@register_tool(
    name="pkb_surface_route",
    description=(
        "Classify a query into one of five intent buckets "
        "(compiled_summary / specific_fact / relational / "
        "personal_context / mixed) and pick the knowledge "
        "surfaces to consult. **Use when** an agent or "
        "orchestrator needs to decide *which retrieval surface* "
        "(wiki / vector / graph / memory) to consult before "
        "spending the query budget. Cheap — regex-only on the "
        "fast path. **Returns** `{primary, surfaces, intent, "
        "confidence, rationale, matched_entity_hint}`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language query to classify.",
            },
        },
        "required": ["query"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "primary": {
                "type": "string",
                "enum": ["wiki", "vector", "graph", "memory"],
            },
            "surfaces": {
                "type": "array",
                "items": {"type": "string", "enum": ["wiki", "vector", "graph", "memory"]},
            },
            "intent": {"type": "string"},
            "confidence": {"type": "number"},
            "rationale": {"type": "string"},
            "matched_entity_hint": {"type": ["string", "null"]},
        },
        "required": ["primary", "surfaces", "intent", "confidence"],
    },
    cost_class="low",
)
async def pkb_surface_route(query: str) -> dict[str, Any]:
    if not query or not query.strip():
        raise InvalidParamsError("query must be a non-empty string")

    decision = _route(query.strip())
    return {
        "primary": decision.primary,
        "surfaces": decision.surfaces,
        "intent": decision.intent,
        "confidence": decision.confidence,
        "rationale": decision.rationale,
        "matched_entity_hint": decision.matched_entity_hint,
    }
