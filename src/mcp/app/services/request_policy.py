# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Transport-boundary construction of the request policy context (E1 Phase 1).

This is the app-layer half of the request-policy seam: it reads the ambient
state a transport has access to — the global Private-Mode level (Redis), the
``X-Client-ID`` consumer identity, and ``CONSUMER_REGISTRY`` — and produces the
immutable :class:`~core.agents.request_context.RequestContext` snapshot that the
core guarded seam enforces. ``core/`` cannot import ``app/``; this helper lives
in ``app/`` and passes the snapshot down.
"""
from __future__ import annotations

from app.services.private_mode import get_private_mode_level
from config.settings import CONSUMER_REGISTRY
from core.agents.request_context import RequestContext


def resolve_consumer(client_id: str) -> dict:
    """Look up a consumer's policy from the registry, falling back to
    ``_default`` for unknown ids (mirrors the canonical /agent/query handler)."""
    return CONSUMER_REGISTRY.get(client_id, CONSUMER_REGISTRY.get("_default", {}))


def resolve_cost_sensitivity(request_value: str | None, client_id: str = "gui") -> str:
    """Resolve the effective cost-sensitivity for a routing decision (E1 CR-028).

    Precedence: an explicit request value wins; else the consumer's registry
    default (``cost_sensitivity`` key); else the persisted global
    ``config.COST_SENSITIVITY`` setting; else ``"medium"``. This makes the
    /agent/query field's "resolved from the consumer registry" promise real and
    lets the persisted GUI cost setting steer the chat smart-router even when the
    client sends no per-request value (E1 CR-026/028).
    """
    if request_value:
        return request_value
    consumer_cs = resolve_consumer(client_id).get("cost_sensitivity")
    if consumer_cs:
        return str(consumer_cs)
    import config
    return getattr(config, "COST_SENSITIVITY", "medium") or "medium"


def build_request_context(
    *,
    client_id: str = "gui",
    strict_domains: bool | None = None,
    skip_cache: bool = False,
    metadata_filter: dict | None = None,
    budget_seconds: float | None = None,
    rag_mode: str = "manual",
    private_level: int | None = None,
) -> RequestContext:
    """Resolve the per-request policy context at a transport boundary.

    ``strict_domains`` from the request can only tighten (True) the consumer
    default, never loosen it — matching the canonical handler's semantics.
    ``private_level`` is read from trusted server state (never the request) when
    not supplied, so a caller cannot forge its way past Private Mode.
    """
    consumer = resolve_consumer(client_id)
    allowed = consumer.get("allowed_domains")
    consumer_strict = bool(consumer.get("strict_domains", False))
    effective_strict = bool(strict_domains) if strict_domains else consumer_strict
    level = private_level if private_level is not None else get_private_mode_level()

    return RequestContext(
        client_id=client_id,
        allowed_domains=tuple(allowed) if allowed is not None else None,
        strict_domains=effective_strict,
        private_level=level,
        skip_cache=skip_cache,
        metadata_filter=metadata_filter,
        budget_seconds=budget_seconds,
        rag_mode=rag_mode,
    )
