# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Forget tools: find what matches, preview with a confirm token, execute.

An MCP client forgets in three steps, and the middle one is for the person:
``pkb_forget_search`` lists candidates; ``pkb_forget_preview`` shows what a
chosen set would remove and returns a single-use token bound to exactly that
set and mode; ``pkb_forget_execute`` runs only what a token was issued for.
MCP carries no caller role, so in multi-user mode the tools refuse: there is
no way to tell an admin from a member here.
"""
from __future__ import annotations

from typing import Any

import config
from app.tool_registry import InvalidParamsError, PermissionDeniedError, UpstreamUnavailableError, register_tool

_CALLER = "mcp"
_MODES = ("trash", "permanent")
_SCOPE_MIN, _SCOPE_MAX = 2, 300
_MAX_SUBJECTS = 200


def _single_user() -> None:
    if config.CERID_MULTI_USER:
        raise PermissionDeniedError("Forgetting over MCP is not available in multi-user mode; use the web app")


_SUBJECT_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["artifact", "chunk", "memory", "conversation"]},
        "id": {"type": "string"},
    },
    "required": ["kind", "id"],
}


@register_tool(
    name="pkb_forget_search",
    description=(
        "Find everything the knowledge base holds that matches a plain-language description of what to forget: "
        "documents (with the matching passages), documents that mention the same people or things, memories, "
        "and conversations. Deterministic search; nothing is changed. **Use when** a person asks to forget a "
        "topic and you need the candidates to show them. **Returns** `{scope, total, candidates: [{kind, id, "
        "store, label, excerpt, domain, reason, passages}]}`; pass the ones the person picks to "
        "`pkb_forget_preview`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "scope": {"type": "string", "description": "What to forget, in the person's words."},
        },
        "required": ["scope"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "scope": {"type": "string"},
            "total": {"type": "integer"},
            "candidates": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["scope", "total", "candidates"],
    },
    cost_class="medium",
)
async def pkb_forget_search(scope: str) -> dict[str, Any]:
    _single_user()
    scope = (scope or "").strip()
    if len(scope) < _SCOPE_MIN or len(scope) > _SCOPE_MAX:
        raise InvalidParamsError("scope must be 2 to 300 characters")
    from app.services.forget.assist import find_candidates
    candidates = await find_candidates(scope)
    return {"scope": scope, "total": len(candidates), "candidates": candidates}


@register_tool(
    name="pkb_forget_preview",
    description=(
        "Show what forgetting the chosen items would remove, grouped (documents, passages, memories, "
        "conversations), and issue a single-use confirm token for exactly that set and mode, valid 15 minutes. "
        "Nothing is changed. **Use when** the person has picked what to forget; show them the groups and ask "
        "them to confirm before calling `pkb_forget_execute`. **Returns** `{groups, derived_facts, notes, "
        "out_of_reach, mode, confirm_token, expires_in}`; `confirm_token` is empty when nothing is left to "
        "forget."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "subjects": {"type": "array", "items": _SUBJECT_SCHEMA, "maxItems": 200,
                         "description": "Items from pkb_forget_search the person chose."},
            "mode": {"type": "string", "enum": list(_MODES),
                     "description": "trash (restorable from Settings → Data) or permanent."},
        },
        "required": ["subjects", "mode"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "groups": {"type": "array", "items": {"type": "object"}},
            "derived_facts": {"type": "integer"},
            "notes": {"type": "array", "items": {"type": "string"}},
            "out_of_reach": {"type": "array", "items": {"type": "string"}},
            "mode": {"type": "string"},
            "confirm_token": {"type": "string"},
            "expires_in": {"type": "integer"},
        },
        "required": ["groups", "mode", "confirm_token", "expires_in"],
    },
    cost_class="medium",
)
async def pkb_forget_preview(subjects: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    import asyncio

    from app.deps import get_redis
    from app.services.forget import confirm
    from app.services.forget.subjects import parse_subjects

    _single_user()
    if mode not in _MODES:
        raise InvalidParamsError("mode must be trash or permanent")
    if not isinstance(subjects, list) or len(subjects) > _MAX_SUBJECTS:
        raise InvalidParamsError("subjects must be a list of at most 200 items")
    try:
        parsed = parse_subjects(subjects)
    except ValueError as exc:
        raise InvalidParamsError(str(exc)) from exc
    return await asyncio.to_thread(confirm.preview_for_token, get_redis(), parsed, mode, _CALLER)


@register_tool(
    name="pkb_forget_execute",
    description=(
        "Forget what a confirm token from `pkb_forget_preview` was issued for: move it to the Trash or erase it "
        "permanently, as previewed. The token works once. **Use when** the person has confirmed the preview. "
        "Never call it without their confirmation. **Returns** `{forget_id, state, subjects}`; state is "
        "trashed, purged, or trashed_pending when a store is still erasing."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "confirm_token": {"type": "string", "description": "The token pkb_forget_preview returned."},
        },
        "required": ["confirm_token"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "forget_id": {"type": "string"},
            "state": {"type": "string", "enum": ["trashed", "purged", "trashed_pending"]},
            "subjects": {"type": "integer"},
        },
        "required": ["forget_id", "state", "subjects"],
    },
    cost_class="medium",
)
async def pkb_forget_execute(confirm_token: str) -> dict[str, Any]:
    import asyncio

    from app.deps import get_redis
    from app.services.forget import confirm, engine

    _single_user()
    try:
        return await asyncio.to_thread(confirm.execute, get_redis(), confirm_token, _CALLER, requested_by="mcp")
    except confirm.ConfirmTokenError as exc:
        raise InvalidParamsError(str(exc)) from exc
    except engine.ForgetUnavailable as exc:
        raise UpstreamUnavailableError(str(exc)) from exc
