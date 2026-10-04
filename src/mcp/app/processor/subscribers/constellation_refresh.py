# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Enqueue ``ComputeUmap3DJob`` when ingestion adds entities.

The Constellation projection ("the cathedral") should grow as the corpus
grows — not once a night. A single GLOBAL Redis debounce
(``cerid:constellation:debounce``) coalesces bulk ingests into one
recompute: the projection covers every entity, so per-entity debouncing
(the wiki_refresh pattern) would be wasted work.

The debounce is sized to what the job COSTS, not fixed. Its TTL is the larger
of ``CONSTELLATION_REFRESH_DEBOUNCE_TTL`` (default 180s) and
``CONSTELLATION_REFRESH_DUTY_FACTOR`` (default 10) times the last run's
measured wall time, which the job records. A fixed 180s assumed a sub-second
job; at ~9K entities the force, wells and domain layouts take ~10 minutes, so
any ingest more than 3 minutes after a run re-armed it, and a steady trickle of
ingests kept one core pegged around the clock. Fail-open when Redis is
unavailable (the job is idempotent), and the nightly schedule still runs.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.subscribers.constellation_refresh")

_DEBOUNCE_KEY = "cerid:constellation:debounce"
_DEFAULT_DEBOUNCE_TTL_S = 180


_LAST_RUN_KEY = "cerid:constellation:last_run_s"
_DEFAULT_DUTY_FACTOR = 10.0


def _debounce_ttl() -> int:
    try:
        return max(0, int(os.environ.get("CONSTELLATION_REFRESH_DEBOUNCE_TTL", _DEFAULT_DEBOUNCE_TTL_S)))
    except (TypeError, ValueError):
        return _DEFAULT_DEBOUNCE_TTL_S


def _duty_factor() -> float:
    try:
        return max(0.0, float(os.environ.get("CONSTELLATION_REFRESH_DUTY_FACTOR", _DEFAULT_DUTY_FACTOR)))
    except (TypeError, ValueError):
        return _DEFAULT_DUTY_FACTOR


def _effective_ttl(redis: Any) -> int:
    """The debounce TTL: the configured floor, or duty factor x the last run's wall time."""
    floor = _debounce_ttl()
    try:
        last = float(redis.get(_LAST_RUN_KEY) or 0)
    except (TypeError, ValueError):
        return floor
    return max(floor, int(last * _duty_factor()))


def record_last_run_seconds(seconds: float) -> None:
    """Called by ComputeUmap3DJob after a completed run. Best-effort."""
    try:
        from app.deps import get_redis  # noqa: PLC0415

        redis = get_redis()
        if redis is not None:
            redis.set(_LAST_RUN_KEY, f"{max(0.0, seconds):.1f}")
    except Exception as exc:  # noqa: BLE001 — bookkeeping only
        log_swallowed_error("processor.subscribers.constellation_refresh.record_run", exc)


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
        acquired = redis.set(_DEBOUNCE_KEY, "1", nx=True, ex=_effective_ttl(redis))
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
