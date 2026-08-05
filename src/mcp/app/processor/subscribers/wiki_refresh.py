# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Enqueue ``WikiRefreshJob`` for entities surfaced by ingest events.

Per-entity Redis debounce (``cerid:wiki:debounce:{slug}``, TTL via
``WIKI_REFRESH_DEBOUNCE_TTL``, default 300s) prevents bulk-ingest
write amplification. ``enqueue_refresh(slug, force=True)`` bypasses
the debounce for contradiction-triggered refreshes. Fail-open when
Redis is unavailable — under-refreshing is worse than over-refreshing.

Queue-level duplicate collapse (``enqueue_job_if_absent``) additionally
skips slugs whose refresh is already pending or running — the debounce
TTL alone cannot cover a queue backlog longer than 300s.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.subscribers.wiki_refresh")

_DEBOUNCE_KEY_FMT = "cerid:wiki:debounce:{slug}"
_DEFAULT_DEBOUNCE_TTL_S = 300  # 5 minutes — tunable via env

# How long a human edit suppresses automatic re-summarisation (grew-trigger +
# stale-sweep).  7 days strikes a balance: the user's edit stays protected for
# a week before the auto-refresh loop can overwrite it.  Contradiction-forced
# refreshes (force=True) always bypass this window regardless.
HUMAN_EDIT_PROTECT_WINDOW_S: int = 7 * 24 * 3600  # 7 days


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


def _is_human_edit_protected(slug: str) -> bool:
    """Return True when the entity's summary was last written by a human and the
    protection window has not expired.

    Queries Neo4j for ``summary_edited_by`` and ``summary_updated_at`` on the
    entity node.  Returns False (not protected) when Neo4j is unavailable or
    when the entity does not exist — fail-open so a broken DB does not
    permanently silence refreshes.

    Contradiction-forced refreshes bypass this check entirely (callers that pass
    ``force=True`` to :func:`enqueue_refresh` skip this function).
    """
    try:
        from datetime import datetime, timedelta, timezone

        from app.deps import get_neo4j  # noqa: PLC0415

        driver = get_neo4j()
        if driver is None:
            return False
        with driver.session() as session:
            row = session.run(
                """
                MATCH (e:Entity {canonical_id: $slug})
                RETURN e.summary_edited_by  AS summary_edited_by,
                       e.summary_updated_at AS summary_updated_at
                LIMIT 1
                """,
                slug=slug,
            ).single()
        if row is None:
            return False
        if row["summary_edited_by"] != "user":
            return False
        updated_at_str: str | None = row["summary_updated_at"]
        if not updated_at_str:
            return False
        try:
            updated_at = datetime.fromisoformat(updated_at_str)
        except ValueError:
            return False
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(tz=timezone.utc) - timedelta(seconds=HUMAN_EDIT_PROTECT_WINDOW_S)
        return updated_at >= cutoff
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "processor.subscribers.wiki_refresh.human_edit_check",
            exc,
            context={"slug": slug},
        )
        return False


def _human_edit_protected_slugs(driver: Any, slugs: list[str]) -> set[str]:
    """Return the subset of ``slugs`` that are within the human-edit protection window.

    Issues ONE Cypher query for all slugs instead of N per-slug queries.
    Applies the same tz-aware comparison logic as :func:`_is_human_edit_protected`,
    including the ``tzinfo is None → replace(timezone.utc)`` guard and fail-open
    on parse errors.  Returns an empty set on any driver error (fail-open: an
    inaccessible DB must not permanently silence refreshes).
    """
    if not slugs:
        return set()
    from datetime import datetime, timedelta, timezone

    try:
        with driver.session() as session:
            rows = session.run(
                """
                MATCH (e:Entity)
                WHERE e.canonical_id IN $slugs
                  AND e.summary_edited_by = 'user'
                RETURN e.canonical_id AS canonical_id,
                       e.summary_updated_at AS summary_updated_at
                """,
                slugs=slugs,
            ).data()
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "processor.subscribers.wiki_refresh.batch_human_edit_check",
            exc,
            context={"slug_count": len(slugs)},
        )
        return set()

    cutoff = datetime.now(tz=timezone.utc) - timedelta(seconds=HUMAN_EDIT_PROTECT_WINDOW_S)
    protected: set[str] = set()
    for row in rows:
        slug = row.get("canonical_id")
        updated_at_str: str | None = row.get("summary_updated_at")
        if not slug or not updated_at_str:
            continue
        try:
            updated_at = datetime.fromisoformat(updated_at_str)
        except ValueError:
            continue
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        if updated_at >= cutoff:
            protected.add(slug)
    return protected


def enqueue_refresh(slug: str, *, force: bool = False) -> bool:
    """Enqueue a WikiRefreshJob for ``slug`` after debounce check.

    Returns True if the job was enqueued, False if debounced out or an
    equivalent refresh for the same slug is already pending/running.
    ``force=True`` bypasses the debounce check (for contradiction-
    triggered refreshes in Phase K2.3) but NOT the queue-level duplicate
    collapse: a pending refresh for the slug will read the latest state
    (contradiction included) when it runs, so stacking a second copy is
    pure queue growth. The nightly stale sweep re-enqueues any slug a
    running-job collapse skipped.
    """
    if not slug:
        return False

    if not force and not _try_acquire_debounce(slug):
        logger.debug("wiki_refresh.debounced slug=%s", slug)
        return False

    try:
        from app.db.redis.processor_queue import enqueue_job_if_absent  # noqa: PLC0415
        from app.processor.jobs.wiki_refresh import WikiRefreshJob  # noqa: PLC0415

        payload = {"entity_slug": slug}
        job = WikiRefreshJob(**payload)
        job_id = enqueue_job_if_absent(job, payload=payload)
        if job_id is None:
            logger.debug("wiki_refresh.collapsed slug=%s (already pending/running)", slug)
            return False
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
    """Handler for the ``entities_added`` event.

    Enqueues a DEBOUNCED (force=False) refresh for every entity slug in the
    event — including slugs for entities that already have a summary (the
    "existing-entity-grew" trigger, WK4).  The 300s per-entity debounce
    prevents write amplification on bulk ingest.

    Skips entities whose summary was last written by a human within the
    ``HUMAN_EDIT_PROTECT_WINDOW_S`` window so the user's manual edit is not
    immediately overwritten.  Contradiction-detected events bypass this via
    ``force=True`` on a separate code path.
    """
    if not _is_enabled():
        return
    slugs: list[str] = payload.get("entity_slugs") or []
    if not slugs:
        return

    # Batch the protection check — one query for all slugs rather than N.
    protected: set[str] = set()
    try:
        from app.deps import get_neo4j  # noqa: PLC0415

        driver = get_neo4j()
        if driver is not None:
            protected = _human_edit_protected_slugs(driver, slugs)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "processor.subscribers.wiki_refresh.batch_protect_lookup",
            exc,
            context={"artifact_id": payload.get("artifact_id")},
        )

    enqueued = 0
    skipped_protected = 0
    for slug in slugs:
        if slug in protected:
            skipped_protected += 1
            logger.debug("wiki_refresh.human_edit_protected slug=%s", slug)
            continue
        if enqueue_refresh(slug):
            enqueued += 1
    logger.info(
        "wiki_refresh.dispatch artifact=%s slugs=%d enqueued=%d skipped_protected=%d",
        payload.get("artifact_id"), len(slugs), enqueued, skipped_protected,
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
