# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Wiki refresh subscriber — Phase K1.3.

Listens for ``entities_added`` events from ``EntityExtractionJob`` and
enqueues ``WikiRefreshJob`` per entity, gated by a per-entity Redis
debounce so a bulk ingest mentioning the same entity 1000 times
doesn't enqueue 1000 refreshes.

Closes the wiki orphan loop: before this subscriber existed,
``WikiRefreshJob`` was defined but never enqueued.

Debounce semantics
------------------
* Redis key: ``cerid:wiki:debounce:{slug}``
* TTL: ``WIKI_REFRESH_DEBOUNCE_TTL`` env var (default 300 s).
* On enqueue, the key is set with NX so concurrent emitters race
  exactly one job in.
* Contradiction-triggered refreshes (the future
  ``contradiction_detected`` event in Phase K2.3) bypass debounce
  by calling ``enqueue_refresh(slug, force=True)``.

Failure isolation
-----------------
Subscriber failures are caught by the event_hooks dispatcher; this
module only needs to handle *Redis* unavailability gracefully — when
Redis is down, we log + skip the debounce check and enqueue anyway
(fail-open is better than missing a refresh).
"""
from __future__ import annotations

import logging
import os
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.subscribers.wiki_refresh")

_DEBOUNCE_KEY_FMT = "cerid:wiki:debounce:{slug}"
_DEFAULT_DEBOUNCE_TTL_S = 300  # 5 minutes — tunable via env


def _debounce_ttl() -> int:
    try:
        return max(0, int(os.environ.get("WIKI_REFRESH_DEBOUNCE_TTL", _DEFAULT_DEBOUNCE_TTL_S)))
    except (TypeError, ValueError):
        return _DEFAULT_DEBOUNCE_TTL_S


def _is_enabled() -> bool:
    """Returns True when the subscriber should fire.

    Operators can disable it via ``CERID_WIKI_REFRESH_ON_INGEST=false``
    to revert to the orphaned (pre-K1.3) behaviour. Default ON.
    """
    val = os.environ.get("CERID_WIKI_REFRESH_ON_INGEST", "true").strip().lower()
    return val in ("true", "1", "yes", "on")


def _try_acquire_debounce(slug: str) -> bool:
    """Set the debounce key with NX. Returns True if we acquired (caller proceeds).

    Returns True on Redis failure (fail-open) so a broken Redis can't
    silently swallow refreshes — the orphan-loop bug was bad enough
    we'd rather over-refresh than under-refresh.
    """
    try:
        from app.deps import get_redis  # noqa: PLC0415

        redis = get_redis()
        if redis is None:
            return True
        key = _DEBOUNCE_KEY_FMT.format(slug=slug)
        # SET NX EX — atomic set-if-not-exists with TTL
        acquired = redis.set(key, "1", nx=True, ex=_debounce_ttl())
        return bool(acquired)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "processor.subscribers.wiki_refresh.debounce",
            exc,
            context={"slug": slug},
        )
        return True


def enqueue_refresh(slug: str, *, force: bool = False) -> bool:
    """Enqueue a WikiRefreshJob for ``slug`` after debounce check.

    Returns True if the job was enqueued, False if debounced out.
    ``force=True`` bypasses the debounce check (for contradiction-
    triggered refreshes in Phase K2.3).
    """
    if not slug:
        return False

    if not force and not _try_acquire_debounce(slug):
        logger.debug("wiki_refresh.debounced slug=%s", slug)
        return False

    try:
        from app.db.redis.processor_queue import enqueue_job  # noqa: PLC0415
        from app.processor.jobs.wiki_refresh import WikiRefreshJob  # noqa: PLC0415

        payload = {"entity_slug": slug}
        job = WikiRefreshJob(**payload)
        enqueue_job(job, payload=payload)
        logger.info("wiki_refresh.enqueued slug=%s force=%s", slug, force)
        return True
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "processor.subscribers.wiki_refresh.enqueue",
            exc,
            context={"slug": slug, "force": force},
        )
        return False


def _on_entities_added(payload: dict[str, Any]) -> None:
    """Handler for the ``entities_added`` event."""
    if not _is_enabled():
        return
    slugs = payload.get("entity_slugs") or []
    if not slugs:
        return
    enqueued = 0
    for slug in slugs:
        if enqueue_refresh(slug):
            enqueued += 1
    logger.info(
        "wiki_refresh.dispatch artifact=%s slugs=%d enqueued=%d",
        payload.get("artifact_id"), len(slugs), enqueued,
    )


def _on_contradiction_detected(payload: dict[str, Any]) -> None:
    """Handler for the ``contradiction_detected`` event (Phase K2.3).

    Contradictions bypass debounce — when the corpus disagrees with
    itself, the user deserves a fresh summary now.
    """
    if not _is_enabled():
        return
    slug = payload.get("entity_slug")
    if not slug:
        return
    enqueue_refresh(slug, force=True)


def register() -> None:
    """Idempotent registration with the event hooks bus.

    Called from ``app.processor.event_hooks`` at module load.
    """
    from app.processor.event_hooks import subscribe  # noqa: PLC0415

    subscribe("entities_added", _on_entities_added)
    subscribe("contradiction_detected", _on_contradiction_detected)
