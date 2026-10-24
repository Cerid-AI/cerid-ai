# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The one rule for whether a call may carry content off the host.

Under the local-only profile no path may send a query, a claim, a document or
a conversation to a non-local endpoint: cloud LLMs, the chat fallback pool,
external data sources, web search, URL fetches made on the user's behalf. Private Mode does not enter
into it; it governs storage and the knowledge base (ruled 2026-10-10).

Every outbound chokepoint asks here instead of reading the flag itself, so a
new path cannot pick its own, weaker reading of the rule.
"""
from __future__ import annotations

import config

REFUSAL = "the local-only profile sends nothing off this machine"


class EgressRefused(RuntimeError):
    """A call that would leave the host was refused by the egress rule."""

    def __init__(self, what: str) -> None:
        super().__init__(f"{what} refused: {REFUSAL}")
        self.what = what


def cloud_egress_refused() -> str:
    """Why content may not leave the host now, or "" when it may.

    Either signal of the profile refuses: the profile itself, or the flag it
    sets at boot (``ALLOW_CLOUD_EGRESS_WHEN_LOCAL=false``, which is also how a
    cloud profile resolved down to local-only arrives).
    """
    if getattr(config, "CERID_ENVIRONMENT_PROFILE", "") == "local-only":
        return REFUSAL
    if not getattr(config, "ALLOW_CLOUD_EGRESS_WHEN_LOCAL", True):
        return f"{REFUSAL} (ALLOW_CLOUD_EGRESS_WHEN_LOCAL=false)"
    return ""


def require_cloud_egress(what: str) -> None:
    """Raise :class:`EgressRefused` naming *what* when the rule forbids egress."""
    if cloud_egress_refused():
        raise EgressRefused(what)
