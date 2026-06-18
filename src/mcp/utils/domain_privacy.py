# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Domain-level retrieval privacy filter (deferred Phase D.2 cleanup).

Some ingest domains carry data that must NOT surface in chat answers
unless the user has explicitly opted in to a higher private-mode level:

    "messages"  →  requires private_mode level ≥ 2

The contract documented in docs/PRO_MESSAGES.md states the iMessage
connector ingests opt-in conversations BUT retrieval will not surface
their content unless the active session has private_mode Level 2+.

This module is the single source of truth for that filter. Callers
into pkb_search_filtered, query_knowledge, and any direct ChromaDB
collection lookup go through ``visible_domains()`` to drop the
privacy-gated entries.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("ai-companion.domain_privacy")


# Privacy-gated domains and the minimum private_mode level required to
# include them in retrieval. Operators can extend the mapping via the
# config layer; the defaults below match the documented contracts.
DOMAIN_PRIVACY_FLOOR: dict[str, int] = {
    "messages": 2,    # iMessage conversations
    "imessage": 2,    # alias used by some early callers; kept for safety
}


def visible_domains(
    requested: list[str] | None,
    private_mode_level: int,
) -> list[str] | None:
    """Filter `requested` to the subset visible at `private_mode_level`.

    - `requested=None` means "no explicit narrowing" — we return None
      so the caller continues to scan all configured domains.  Callers
      that want the privacy filter applied to the all-domains case
      must pass the full domain list explicitly.
    - Returns a new list (never mutates the input).
    - When the filter removes domains, logs a single INFO line so
      operators can correlate "missing iMessage results" with the
      active private_mode level.
    """
    if requested is None:
        return None
    filtered = [d for d in requested if _domain_visible(d, private_mode_level)]
    dropped = set(requested) - set(filtered)
    if dropped:
        logger.info(
            "domain_privacy: hid %d domain(s) at private_mode=%d: %s",
            len(dropped), private_mode_level, sorted(dropped),
        )
    return filtered


def _domain_visible(domain: str, level: int) -> bool:
    floor = DOMAIN_PRIVACY_FLOOR.get(domain)
    if floor is None:
        return True  # not privacy-gated
    return level >= floor


def is_domain_visible(domain: str, private_mode_level: int) -> bool:
    """Single-domain variant for callers that don't have a list."""
    return _domain_visible(domain, private_mode_level)


def get_global_private_mode_level() -> int:
    """Read the process-wide private_mode level from Redis. Returns 0
    on any error or when Redis is unavailable (privacy-defaulting:
    when in doubt, treat as "no elevated permissions").
    """
    try:
        from app.deps import get_redis
        redis = get_redis()
        if redis is None:
            return 0
        raw = redis.get("cerid:private_mode:global")
        return int(raw) if raw is not None else 0
    except Exception:  # noqa: BLE001 — Redis/DI can fail many ways
        return 0
