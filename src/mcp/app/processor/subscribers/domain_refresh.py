# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Enqueue ``DeriveDomainsJob`` when ingestion adds entities.

Domain derivation covers every entity, so per-entity debouncing would be
wasted work — the same rationale as constellation_refresh.py:8-13.
A single GLOBAL Redis debounce (``cerid:domains:derive:debounce``, TTL via
``DOMAIN_REFRESH_DEBOUNCE_TTL``, default 180 s) coalesces bulk ingests into
one recompute. Fail-open when Redis is unavailable — the job is cheap and
idempotent.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.subscribers.domain_refresh")

_DEBOUNCE_KEY = "cerid:domains:derive:debounce"
_DEFAULT_DEBOUNCE_TTL_S = 180


def _debounce_ttl() -> int:
    try:
        return max(0, int(os.environ.get("DOMAIN_REFRESH_DEBOUNCE_TTL", _DEFAULT_DEBOUNCE_TTL_S)))
    except (TypeError, ValueError):
        return _DEFAULT_DEBOUNCE_TTL_S


def _is_enabled() -> bool:
    """Operators can disable via ``CERID_DOMAIN_REFRESH_ON_INGEST=false``."""
    val = os.environ.get("CERID_DOMAIN_REFRESH_ON_INGEST", "true").strip().lower()
    return val in ("true", "1", "yes", "on")


def _try_acquire_debounce() -> bool:
    """SET NX on the global debounce key. Fail-open on Redis trouble."""
    try:
        from app.deps import get_redis  # noqa: PLC0415

        redis = get_redis()
        if redis is None:
            return True
        acquired = redis.set(_DEBOUNCE_KEY, "1", nx=True, ex=_debounce_ttl())
        return bool(acquired)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("processor.subscribers.domain_refresh.debounce", exc)
        return True


def _on_entities_added(payload: dict[str, Any]) -> None:
    """Handler for ``entities_added`` — re-derive domain fields on all entities."""
    if not _is_enabled():
        return
    if not payload.get("entity_slugs"):
        return
    if not _try_acquire_debounce():
        logger.debug("domain_refresh.debounced")
        return

    try:
        from app.db.redis.processor_queue import enqueue_job  # noqa: PLC0415
        from app.processor.jobs.derive_domains import DeriveDomainsJob  # noqa: PLC0415

        job = DeriveDomainsJob()
        enqueue_job(job, payload={})
        logger.info(
            "domain_refresh.enqueued artifact=%s entities=%d",
            payload.get("artifact_id"),
            len(payload.get("entity_slugs") or []),
        )
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("processor.subscribers.domain_refresh.enqueue", exc)


def register() -> None:
    """Idempotent registration with the event hooks bus."""
    from app.processor.event_hooks import subscribe  # noqa: PLC0415

    subscribe("entities_added", _on_entities_added)
