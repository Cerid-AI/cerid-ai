# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Enqueue ``ComputeUmap3DJob`` when ingestion adds entities.

The Constellation projection ("the cathedral") should grow as the corpus
grows — not once a night. A single GLOBAL Redis debounce
(``cerid:constellation:debounce``, TTL via
``CONSTELLATION_REFRESH_DEBOUNCE_TTL``, default 180s) coalesces bulk
ingests into one recompute: the projection covers every entity, so
per-entity debouncing (the wiki_refresh pattern) would be wasted work.
Fail-open when Redis is unavailable — the job is cheap (fallback layout
of ~3K entities runs in <1s) and idempotent.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.subscribers.constellation_refresh")

_DEBOUNCE_KEY = "cerid:constellation:debounce"
_DEFAULT_DEBOUNCE_TTL_S = 180


def _debounce_ttl() -> int:
    try:
        return max(0, int(os.environ.get("CONSTELLATION_REFRESH_DEBOUNCE_TTL", _DEFAULT_DEBOUNCE_TTL_S)))
    except (TypeError, ValueError):
        return _DEFAULT_DEBOUNCE_TTL_S


def _is_enabled() -> bool:
    """Operators can disable via ``CERID_CONSTELLATION_REFRESH_ON_INGEST=false``."""
    val = os.environ.get("CERID_CONSTELLATION_REFRESH_ON_INGEST", "true").strip().lower()
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
        log_swallowed_error("processor.subscribers.constellation_refresh.debounce", exc)
        return True


def _on_entities_added(payload: dict[str, Any]) -> None:
    """Handler for ``entities_added`` — recompute the 3D projection."""
    if not _is_enabled():
        return
    if not payload.get("entity_slugs"):
        return
    # Cheap first-line filter that coalesces ingest bursts into one recompute.
    # The authoritative dedup is now enqueue_job_if_absent below — it is
    # running-set-aware and collapses a concurrent duplicate even when a job's
    # work outlives this debounce's TTL (which the bare enqueue could not).
    if not _try_acquire_debounce():
        logger.debug("constellation_refresh.debounced")
        return

    try:
        from app.db.redis.processor_queue import enqueue_job_if_absent  # noqa: PLC0415
        from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob  # noqa: PLC0415

        job = ComputeUmap3DJob()
        job_id = enqueue_job_if_absent(job, payload={})
        if job_id is None:
            logger.debug(
                "constellation_refresh.collapsed onto an in-flight job artifact=%s",
                payload.get("artifact_id"),
            )
            return
        logger.info(
            "constellation_refresh.enqueued artifact=%s entities=%d",
            payload.get("artifact_id"),
            len(payload.get("entity_slugs") or []),
        )
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error("processor.subscribers.constellation_refresh.enqueue", exc)


def register() -> None:
    """Idempotent registration with the event hooks bus."""
    from app.processor.event_hooks import subscribe  # noqa: PLC0415

    subscribe("entities_added", _on_entities_added)
