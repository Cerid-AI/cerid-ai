# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Strict-agents kill switch for regulated deployments (Sprint 1C).

Cerid's user-defined custom agents (``app.routers.custom_agents``) let
end-users compose system prompts, tool allowlists, KB domain scopes,
and model overrides. In a regulated deployment that capability may be
unacceptable: the system must run only the audited built-in 10 agents
(``core.agents.*``). This module is the single environment-variable
gate that disables the custom-agents surface entirely.

Set ``STRICT_AGENTS_ONLY=true`` (or ``1``, ``yes``, ``on``) to lock
down. Default is ``false`` — the open / developer posture.

The flag is read **per call**, not at module import, per the
``module-level os.getenv capture`` lesson — operators can flip it
without restarting the process.

Enforcement is wired as a router-level FastAPI dependency on the
custom-agents router; every CRUD endpoint, template endpoint, and
runtime path runs the gate before its body executes. Reads are
locked down too — operators do data inspection / cleanup via direct
Neo4j tooling (see runbook) before flipping the flag.
"""
from __future__ import annotations

import logging
import os

from fastapi import HTTPException, status

logger = logging.getLogger("ai-companion.strict_agents_policy")

ENV_STRICT = "STRICT_AGENTS_ONLY"
_TRUTHY = frozenset({"true", "1", "yes", "on"})


def is_strict_mode() -> bool:
    """Return ``True`` when ``STRICT_AGENTS_ONLY`` is set to a truthy value.

    Accepted truthy values (case-insensitive, whitespace-trimmed):
    ``true``, ``1``, ``yes``, ``on``. Anything else — including unset,
    empty string, ``false``, ``0`` — returns ``False``.

    Read at request time so an operator can flip the flag without
    restarting; module-level capture would freeze the value at boot
    (see ``tasks/lessons.md`` → "Module-level os.getenv captures").
    """
    return os.getenv(ENV_STRICT, "").strip().lower() in _TRUTHY


def enforce_strict_mode() -> None:
    """FastAPI dependency that blocks every custom-agents endpoint when the
    kill switch is on.

    Wired as ``router = APIRouter(..., dependencies=[Depends(enforce_strict_mode)])``
    on the custom-agents router. The dependency runs before any
    endpoint body, so the 403 fires before Neo4j is touched, before
    Pydantic validates the body, and before the runtime can load an
    agent definition.

    Logs at WARNING when a denial fires so the audit trail captures
    attempted access — useful for incident response in regulated
    environments where attempted use of disabled features is itself
    a signal.
    """
    if not is_strict_mode():
        return
    logger.warning(
        "custom-agents request denied: %s=true (regulated deployment)",
        ENV_STRICT,
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            "Custom agents are disabled in this deployment "
            f"({ENV_STRICT}=true). Built-in agents only."
        ),
    )
