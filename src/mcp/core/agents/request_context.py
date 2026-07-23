# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resolved per-request policy for the chat/retrieval seam (E1 Phase 1).

The E1 audit's unifying root cause: cross-cutting request policy — consumer
domain isolation, Private-Mode gating, per-request retrieval directives — was
enforced only in the canonical ``/agent/query`` handler, so every alternate
transport (MCP, A2A, legacy ``/query``, custom-agents, ``/agent/memory/recall``,
automations) re-entered the pipeline having dropped some of it.

:class:`RequestContext` is the single resolved policy object. It is constructed
exactly once at each transport boundary (app-layer, where Redis / request
headers / ``CONSUMER_REGISTRY`` are reachable — see
``app.services.request_policy.build_request_context``) and threaded into the
shared guarded retrieval seam (``core.agents.guarded_retrieval``), which enforces
it. This lives in ``core/`` and imports nothing from ``app/`` (the import-linter
boundary): the transport reads the ambient state and passes a *snapshot* in,
mirroring the ``create_memory_fn`` DI pattern already used by the streaming
verifier.
"""
from __future__ import annotations

from dataclasses import dataclass

# Private-Mode level at which KB/memory retrieval must be bypassed server-side
# ("skip KB"). Mirrors app/services/private_mode.py + the web client's tiers.
PRIVATE_MODE_SKIP_KB_LEVEL = 2


@dataclass(frozen=True)
class RequestContext:
    """Immutable snapshot of the policy a single request must be evaluated under.

    ``allowed_domains`` is a tuple (or ``None`` = all domains) so the context is
    hashable/frozen; the guarded seam converts back to a list for the retrieval
    call. ``private_level`` is the global Private-Mode level read once at the
    boundary (0 = off).
    """

    client_id: str = "gui"
    allowed_domains: tuple[str, ...] | None = None
    strict_domains: bool = False
    private_level: int = 0
    skip_cache: bool = False
    metadata_filter: dict | None = None
    budget_seconds: float | None = None
    rag_mode: str = "manual"

    @property
    def blocks_kb(self) -> bool:
        """True when Private Mode forbids KB/memory retrieval for this request."""
        return self.private_level >= PRIVATE_MODE_SKIP_KB_LEVEL

    def allowed_domains_list(self) -> list[str] | None:
        return list(self.allowed_domains) if self.allowed_domains is not None else None
