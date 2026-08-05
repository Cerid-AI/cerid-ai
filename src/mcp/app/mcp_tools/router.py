# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""``pkb_surface_route`` — expose the surface router as an MCP tool.

Lets orchestration agents (Boardroom, custom templates) ask which
surfaces to consult without re-implementing the routing heuristics.
Backed by ``core.retrieval.surface_router``.
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
