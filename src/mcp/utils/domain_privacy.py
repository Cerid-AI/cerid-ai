# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Domain-level retrieval privacy filter.

Some ingest domains carry data that must NOT surface in chat answers
unless the user has explicitly opted in via the dedicated
``SENSITIVE_DOMAIN_RETRIEVAL_ENABLED`` toggle:

    "messages" / "imessage"  →  surfaced only when the opt-in is on

This opt-in is a standalone, orthogonal setting — it is INDEPENDENT of the
private-mode isolation level (see app/services/private_mode.py). The
isolation ladder controls how much of a session is persisted/exposed;
raising it must never be the mechanism that reveals sensitive data. The
opt-in defaults OFF, matching today's default-hidden behavior.

The contract documented in docs/PRO_MESSAGES.md states the iMessage
connector ingests opt-in conversations BUT retrieval will not surface
their content unless ``SENSITIVE_DOMAIN_RETRIEVAL_ENABLED`` is also on.

This module is the single source of truth for that filter. Callers
into pkb_search_filtered, the /query + /sdk/v1/search endpoints, and any
direct ChromaDB collection lookup go through ``visible_domains()`` to
drop the privacy-gated entries.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("ai-companion.domain_privacy")


# Domains hidden from retrieval unless the caller opts in via
# sensitive_domains_opted_in(). Operators can extend the set via the config
# layer; the defaults below match the documented contracts.
SENSITIVE_DOMAINS: frozenset[str] = frozenset({"messages", "imessage"})


def visible_domains(
    requested: list[str] | None,
    *,
    include_sensitive: bool,
) -> list[str] | None:
    """Filter `requested` to the subset visible given `include_sensitive`.

    - `requested=None` means "no explicit narrowing" — we return None
      so the caller continues to scan all configured domains.  Callers
      that want the privacy filter applied to the all-domains case
      must pass the full domain list explicitly.
    - Returns a new list (never mutates the input).
    - When the filter removes domains, logs a single INFO line so
      operators can correlate "missing iMessage results" with the
      opt-in state.
    """
    if requested is None:
        return None
    filtered = [d for d in requested if _domain_visible(d, include_sensitive)]
    dropped = set(requested) - set(filtered)
    if dropped:
        logger.info(
            "domain_privacy: hid %d domain(s) (sensitive_domain_retrieval_enabled=%s): %s",
            len(dropped), include_sensitive, sorted(dropped),
        )
    return filtered


def _domain_visible(domain: str, include_sensitive: bool) -> bool:
    if domain not in SENSITIVE_DOMAINS:
        return True  # not privacy-gated
    return include_sensitive


def is_domain_visible(domain: str, *, include_sensitive: bool) -> bool:
    """Single-domain variant for callers that don't have a list."""
    return _domain_visible(domain, include_sensitive)


def sensitive_domains_opted_in() -> bool:
    """Read the dedicated sensitive-domain-retrieval opt-in from config.

    This is the sole visibility source for SENSITIVE_DOMAINS — replaces the
    former private-mode-level coupling. Defaults False (hidden) unless the
    operator has explicitly set SENSITIVE_DOMAIN_RETRIEVAL_ENABLED.
    """
    import config.settings

    return config.settings.SENSITIVE_DOMAIN_RETRIEVAL_ENABLED
