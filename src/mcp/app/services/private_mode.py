# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Server-side Private Mode enforcement (Task 1.1).

Private Mode has historically been a client-side-only convenience: the
level is stored in Redis (``cerid:private_mode:global``), but no server
code path consulted it — ``use-chat.ts`` / ``use-chat-send.ts`` in the
web client did all the skipping. A direct API caller (SDK, curl, a
future mobile client) bypassed privacy entirely.

This module makes Level 1 ("skip saves & sync") a real server
guarantee. Callers gate a write path with::

    from app.services.private_mode import private_blocks

    if private_blocks(1):
        return <silent-success shape matching the endpoint's contract>

Level semantics (mirrors ``app/routers/settings.py::PrivateModeRequest``):
0=off, 1=skip saves, 2=skip KB, 3=skip audit, 4=full ephemeral. Only L1
(write-path gating via ``private_blocks``) is enforced in this module.
L2 (KB bypass) and L3 (audit skip) are enforced at their own call sites
— ``app/routers/query.py`` / ``app/routers/agents.py`` for L2,
``app/tools.py``'s ``mcp.tool_call`` audit line for L3. L4 (full
ephemeral) is additionally backed by the session-wipe orchestrator in
``app/services/session_wipe.py``, invoked from
``POST /settings/private-mode/session-wipe``.

This module only reads the *global* private-mode key. The per-session
override (``cerid:private_mode:session:<id>``) is out of scope — it
exists solely for the L4 session-wipe confirmation flow.
"""
from __future__ import annotations

import logging
from typing import Any

from app.deps import get_redis
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.private_mode")

PRIVATE_MODE_KEY = "cerid:private_mode:global"

# Markers the web client stamps onto the single injected ``system`` message
# (RAG preamble + ``<document>``/``<memory>`` blocks — see
# src/web/src/lib/rag-prompt.ts + kb-utils.ts). A system message carrying any
# of these is KB/memory context, never a plain instruction, so at L2+ the
# generation gate drops it. Privacy-safe by construction: matching is
# permissive (any marker strips) because a false-negative here leaks the
# user's KB to the model, which is the exact failure Private Mode L2 forbids.
_INJECTION_MARKERS = (
    "<document",
    "<memory",
    "[Remembered Context]",
    "The user has a personal knowledge base.",
)


def get_private_mode_level() -> int:
    """Return the current global private-mode level (0 = disabled).

    Fails open to 0 on any error (Redis down, bad value, etc.) —
    privacy gating must never break normal operation, and the client
    applies its own skip logic independently of this server check.
    """
    try:
        redis = get_redis()
        level = redis.get(PRIVATE_MODE_KEY)
        return int(level) if level is not None else 0
    except Exception as exc:
        log_swallowed_error("private_mode.get_level", exc)
        return 0


def private_blocks(threshold: int) -> bool:
    """Return True when the current private-mode level is >= ``threshold``."""
    return get_private_mode_level() >= threshold


def _role_of(message: Any) -> str | None:
    """Role of a chat message, whether it is a Pydantic ``_ChatMessage``
    (attribute access, ``/chat/stream``) or a raw ``dict`` (``/sdk/v1/llm/
    complete``)."""
    if isinstance(message, dict):
        return message.get("role")
    return getattr(message, "role", None)


def _content_of(message: Any) -> str:
    if isinstance(message, dict):
        return message.get("content") or ""
    return getattr(message, "content", None) or ""


def strip_injected_context(messages: list[Any]) -> list[Any]:
    """Enforce the L2+ "bypass KB injection" contract at the generation
    boundary: drop any ``system`` message carrying KB/memory injection markers.

    This is the server-side backstop the web client cannot provide — the web
    client assembles the injected ``system`` message itself, so a direct
    caller (SDK, curl, mobile) that replicates that assembly would otherwise
    reach the model with the user's KB/memory despite Private Mode being on.
    The level is read from trusted server state (:data:`PRIVATE_MODE_KEY`),
    never from the request, so a caller cannot forge its way past this.

    No-op below L2 (returns the input list unchanged). Works structurally on
    role/content so both message shapes pass through unchanged when nothing
    matches.
    """
    if not private_blocks(2):
        return messages

    kept: list[Any] = []
    stripped = 0
    for message in messages:
        if _role_of(message) == "system":
            content = _content_of(message)
            if any(marker in content for marker in _INJECTION_MARKERS):
                stripped += 1
                continue
        kept.append(message)

    if stripped:
        # Count only — never the content (L3 forbids logging conversation
        # content; the count is metadata proving the gate fired).
        logger.info("private_mode: stripped %d injected system message(s) at L2+", stripped)
    return kept
