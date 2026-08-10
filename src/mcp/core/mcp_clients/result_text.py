# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Flatten an MCP tool result to its text.

Lives in ``core`` rather than in a plugin because more than one connector
needs it and a plugin importing another plugin breaks the moment tier gating
stops one of them from loading.

Why it exists at all: ``MCPClientPool.call_tool`` returns the ``mcp`` package's
``CallToolResult``, not a dict. Two DataSources type-checked for ``list`` or
``dict``, matched neither, and silently returned nothing for every query —
which reads as "no mail" or "no events" rather than as a fault. Anything
consuming a tool result should come through here.
"""
from __future__ import annotations

from typing import Any


def is_error_result(raw: Any) -> bool:
    """True when an MCP tool result carries the protocol's error flag.

    MCP reports tool-level failures — unknown tool name, argument validation,
    an upstream 401 — as a *result* with ``isError`` set, not as an exception.
    Nothing in this codebase inspected that flag, so a connector that had
    answered nothing but errors still counted as having succeeded, and the
    operator was told the sibling was reachable (observed against ms365-mcp,
    which 401s every Graph call). Treat an error result as "not a success".
    """
    flag = getattr(raw, "isError", None)
    if flag is None and isinstance(raw, dict):
        flag = raw.get("isError")
    return bool(flag)


def tool_text(raw: Any) -> str:
    """Best-effort text of an MCP tool result.

    Accepts ``CallToolResult``, the plain dict form, or a bare string, and
    returns ``""`` for anything it cannot read — callers treat empty as "no
    results", which is the safe reading for a shape we do not recognise.
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw

    content = getattr(raw, "content", None)
    if content is not None:
        parts = [getattr(c, "text", "") or "" for c in content]
        if any(parts):
            return "\n".join(p for p in parts if p)

    # structuredContent is `{"result": "<the same prose>"}` on the Google
    # Workspace sibling — a string wrapper, not a schema. A fallback, never a
    # preference: do not mistake its presence for structured output.
    structured = getattr(raw, "structuredContent", None)
    if isinstance(structured, dict) and isinstance(structured.get("result"), str):
        return structured["result"]

    if isinstance(raw, dict):
        if isinstance(raw.get("result"), str):
            return raw["result"]
        parts = [c.get("text", "") for c in raw.get("content", []) if isinstance(c, dict)]
        if any(parts):
            return "\n".join(p for p in parts if p)
    return ""
