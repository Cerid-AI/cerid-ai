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

from app.deps import get_redis
from core.utils.swallowed import log_swallowed_error

PRIVATE_MODE_KEY = "cerid:private_mode:global"


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
