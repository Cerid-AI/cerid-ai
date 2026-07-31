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

# Level 1 ("skip saves & sync") is the contract boundary at which every durable
# server-side save of conversation-derived data must stop — conversation saves,
# verified-fact memory promotion, and verification-report persistence alike.
SKIP_SAVES_LEVEL = 1

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
    # E1 CR-080: match the FE's STABLE sentinel (web/src/lib/rag-prompt.ts
    # KB_CONTEXT_SENTINEL), stamped at the head of every KB-injected system
    # message, instead of the human-readable preamble copy. The copy string was
    # string-coupled — a reword on the FE would silently break this L2 backstop
    # and leak the KB to the model. Keep byte-identical to the TS constant.
    "<!--cerid:kb-context-->",
)


# Last level successfully read from Redis. Consulted only when Redis is
# unreachable — see get_private_mode_level.
_last_known_level: int = 0


def get_private_mode_level() -> int:
    """Return the current global private-mode level (0 = disabled).

    On a Redis error this returns the **last level successfully read**, not 0.

    The previous behaviour failed open to 0, reasoning that "the client applies
    its own skip logic independently". That holds only for the browser: direct
    API, SDK and MCP callers have no client-side skip, so a transient Redis
    blip silently deactivated every L1-L4 server-side guarantee for them, with
    nothing in the response to say so. In a privacy-first product a dropped
    guarantee must not be the quiet default.

    Falling back to the last-known level is safe in both directions because the
    level itself lives in Redis: if Redis is unreachable nobody can have changed
    it, so the cached value *is* the current value. It also cannot break a
    normal install — an instance that never enabled private mode caches 0 and
    behaves exactly as before. Only a cold start with Redis already down yields
    0 without ever having observed the real level, and that case is logged.
    """
    global _last_known_level
    try:
        redis = get_redis()
        level = redis.get(PRIVATE_MODE_KEY)
        resolved = int(level) if level is not None else 0
        _last_known_level = resolved
        return resolved
    except Exception as exc:
        log_swallowed_error("private_mode.get_level", exc)
        if _last_known_level:
            logger.warning(
                "private-mode level unreadable (Redis error) — holding last "
                "known level %d rather than failing open to 0",
                _last_known_level,
            )
        return _last_known_level


def private_blocks(threshold: int) -> bool:
    """Return True when the current private-mode level is >= ``threshold``."""
    return get_private_mode_level() >= threshold


def saves_blocked() -> bool:
    """True when Private Mode suppresses durable server-side saves (L1+).

    The single named test for the "skip saves & sync" contract. Every transport
    that would durably persist conversation-derived data gates on this so the
    same class of write stops at the same level: conversation history saves,
    verified-fact memory promotion (see
    ``app.routers.agents._verified_memory_fn``), and the verification-report
    twin stores (Redis ``hall:{cid}`` + Neo4j ``:VerificationReport``). Reading
    the level from trusted server state, a direct API/MCP/A2A caller gets the
    same guarantee the web client applies locally. Fails open (L0) on any error,
    like :func:`get_private_mode_level`.
    """
    return private_blocks(SKIP_SAVES_LEVEL)


def seed_private_mode_from_env() -> None:
    """Seed the global private-mode level from the boot env at startup.

    ``CERID_PRIVATE_MODE`` / ``CERID_PRIVATE_MODE_LEVEL`` (materialized as
    ``config.settings.PRIVATE_MODE_ENABLED`` / ``PRIVATE_MODE_LEVEL``) declare a
    hardened install's boot privacy posture, but enforcement reads the level
    only from :data:`PRIVATE_MODE_KEY` in Redis — nothing wired the env into it,
    so the knobs were inert and the server ran at level 0 until the GUI toggle
    (CR-011). This closes that gap: called once from the app lifespan.

    Seeds **only** when the env enables private mode AND the key is currently
    unset, so a level set at runtime via the toolbar survives a restart rather
    than being silently reset to the env default. Best-effort — a Redis error at
    boot must not crash startup (the GUI/API can still set the level later).
    """
    from config import settings

    if not getattr(settings, "PRIVATE_MODE_ENABLED", False):
        return
    try:
        redis = get_redis()
        if redis.get(PRIVATE_MODE_KEY) is None:
            level = int(getattr(settings, "PRIVATE_MODE_LEVEL", 1))
            redis.set(PRIVATE_MODE_KEY, str(level))
            logger.info(
                "private_mode: seeded global level=%d from env (CERID_PRIVATE_MODE)",
                level,
            )
    except Exception as exc:
        log_swallowed_error("private_mode.seed_from_env", exc)


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
