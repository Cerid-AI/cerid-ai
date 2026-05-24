# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Scheduled Maintenance Engine.

Runs background tasks on configurable schedules using APScheduler.
Execution results are logged to Redis for monitoring.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from app.deps import get_chroma, get_neo4j, get_redis
from core.utils.cache import log_event
from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.scheduler")

_scheduler: AsyncIOScheduler | None = None

# Hooks registered by the internal-build bootstrap (see bottom of this file).
# Each callable receives the scheduler instance and may add jobs before start.
_post_setup_hooks: list = []


def _log_execution(job_name: str, status: str, duration: float, detail: str = "") -> None:
    """Log a scheduled job execution to Redis."""
    try:
        redis = get_redis()
        log_event(
            redis,
            event_type="scheduled_job",
            artifact_id="",
            domain="",
            filename="",
            extra={
                "job": job_name,
                "status": status,
                "duration_s": round(duration, 2),
                "detail": detail,
                "timestamp": utcnow_iso(),
            },
        )
    except Exception as e:
        logger.warning(f"Failed to log scheduled job {job_name}: {e}")


async def _run_daily_digest() -> None:
    """Phase K Day 1 — daily LLM-synthesized activity digest.

    Gates (in order):
      1. ``daily_digest`` feature flag (Pro tier — agent enforces this too)
      2. ``CERID_DAILY_DIGEST_ENABLED`` env toggle (operator opt-in)

    Persists the digest as a KB artifact in domain="digests" and
    fires a ``digest.ready`` webhook event for the in-app surface
    to consume. Per-user local-7am is server-UTC-7am for v1; per-user
    timezone resolution is tracked for Phase K.2.
    """
    import os

    from config.features import is_feature_enabled

    start = time.time()
    if not is_feature_enabled("daily_digest"):
        return
    # Redis override wins; env fallback. Surfaced via the
    # Settings → System → Pro Automations card.
    from utils.pro_automations import is_enabled as _automation_enabled
    if not _automation_enabled("daily_digest"):
        return

    try:
        from core.agents.daily_digest import generate_daily_digest
        result = await generate_daily_digest(
            window_hours=int(os.getenv("DAILY_DIGEST_WINDOW_HOURS", "24")),
            persist=True,
        )
        duration = time.time() - start
        msg = (
            f"{result.artifact_count} artifacts, "
            f"flagged={result.flagged_count}, "
            f"inbox_urgent={result.inbox_urgent_count}, "
            f"skipped={result.skipped}"
        )
        _log_execution("daily_digest", "success", duration, msg)
        logger.info("Scheduled daily digest completed: %s", msg)

        # Fire the digest.ready webhook so in-app surfaces (toast,
        # SSE bridge, future email worker) can deliver it.
        if not result.skipped:
            try:
                from utils.webhooks import fire_event
                await fire_event("digest.ready", {
                    "digest_id": result.digest_id,
                    "generated_at": result.generated_at,
                    "artifact_count": result.artifact_count,
                    "flagged_count": result.flagged_count,
                    "inbox_urgent_count": result.inbox_urgent_count,
                    "persisted_artifact_id": result.persisted_artifact_id,
                    "summary": "Your daily digest is ready.",
                })
            except Exception as exc:  # noqa: BLE001
                log_swallowed_error("daily_digest.fire_event", exc)
    except Exception as e:
        duration = time.time() - start
        _log_execution("daily_digest", "error", duration, str(e))
        logger.error(f"Scheduled daily digest failed: {e}")


async def _run_inbox_triage() -> None:
    """Phase J Day 2 — periodic inbox triage.

    Fetches recent unread Gmail + Outlook threads, runs LLM
    categorization per thread, and persists each as a KB artifact in
    domain="inbox". Idempotent: re-runs over the same thread update
    the same artifact via the source_id hash.

    Three guards before doing any work:
      1. inbox_triage feature flag (Pro tier — agent enforces this too)
      2. CERID_INBOX_TRIAGE_ENABLED env toggle (operator opt-in)
      3. At least one of gmail/outlook registered + configured
    """
    import os

    from config.features import is_feature_enabled

    start = time.time()
    if not is_feature_enabled("inbox_triage"):
        return  # feature off — silent skip
    # Redis override wins; env fallback when Redis is unavailable.
    # Surfaced via the Settings → System → Pro Automations card.
    from utils.pro_automations import is_enabled as _automation_enabled
    if not _automation_enabled("inbox_triage"):
        return  # operator hasn't flipped the toggle

    try:
        from core.agents.inbox_triage import triage_inboxes
        result = await triage_inboxes(
            max_results_per_source=int(os.getenv("INBOX_TRIAGE_MAX_PER_SOURCE", "30")),
            persist=True,
        )
        duration = time.time() - start
        msg = (
            f"{len(result.threads)} threads, "
            f"by_category={result.by_category}, "
            f"sources={result.sources_queried}, "
            f"skipped={len(result.skipped)}"
        )
        _log_execution("inbox_triage", "success", duration, msg)
        logger.info("Scheduled inbox triage completed: %s", msg)
    except Exception as e:
        duration = time.time() - start
        _log_execution("inbox_triage", "error", duration, str(e))
        logger.error(f"Scheduled inbox triage failed: {e}")


async def _run_rectify() -> None:
    """Run the rectification agent to find duplicates, orphans, etc."""
    start = time.time()
    try:
        from core.agents.rectify import rectify
        result = await rectify(
            neo4j_driver=get_neo4j(),
            chroma_client=get_chroma(),
            redis_client=get_redis(),
            checks=None,
            auto_fix=False,
            stale_days=config.SCHEDULE_STALE_DAYS,
        )
        findings = result.get("total_findings", 0) if isinstance(result, dict) else 0
        duration = time.time() - start
        _log_execution("rectify", "success", duration, f"{findings} findings")
        logger.info(f"Scheduled rectify completed: {findings} findings in {duration:.1f}s")
        if findings > 0:
            from utils.webhooks import notify_rectify_findings
            await notify_rectify_findings(findings)
    except Exception as e:
        duration = time.time() - start
        _log_execution("rectify", "error", duration, str(e))
        logger.error(f"Scheduled rectify failed: {e}")


async def _run_health_check() -> None:
    """Run a system health check."""
    start = time.time()
    try:
        from app.routers.health import health_check
        result = health_check()
        status = result.get("status", "unknown") if isinstance(result, dict) else "unknown"
        duration = time.time() - start
        _log_execution("health_check", status, duration)
        logger.info(f"Scheduled health check: {status} in {duration:.1f}s")
        if status not in ("healthy", "ok"):
            from utils.webhooks import notify_health_warning
            await notify_health_warning(status)
    except Exception as e:
        duration = time.time() - start
        _log_execution("health_check", "error", duration, str(e))
        logger.error(f"Scheduled health check failed: {e}")


async def _run_stale_detection() -> None:
    """Detect stale artifacts that haven't been accessed recently."""
    start = time.time()
    try:
        from core.agents.maintenance import maintain
        result = await maintain(
            neo4j_driver=get_neo4j(),
            chroma_client=get_chroma(),
            redis_client=get_redis(),
            actions=["stale"],
            stale_days=config.SCHEDULE_STALE_DAYS,
            auto_purge=False,
        )
        stale_count = 0
        if isinstance(result, dict):
            stale_count = len(result.get("stale_artifacts", []))
        duration = time.time() - start
        _log_execution("stale_detection", "success", duration, f"{stale_count} stale")
        logger.info(f"Scheduled stale detection: {stale_count} stale in {duration:.1f}s")
    except Exception as e:
        duration = time.time() - start
        _log_execution("stale_detection", "error", duration, str(e))
        logger.error(f"Scheduled stale detection failed: {e}")


async def _run_sync_export() -> None:
    """Scheduled incremental export to sync directory."""
    start = time.time()
    try:
        from app.sync.export import export_all
        from app.sync.manifest import read_manifest

        # Read last_exported_at from existing manifest for incremental filter
        since = None
        try:
            manifest = read_manifest(config.SYNC_DIR)
            since = manifest.get("last_exported_at")
        except (FileNotFoundError, ValueError):
            pass

        result = export_all(
            driver=get_neo4j(),
            chroma_url=config.CHROMA_URL,
            redis_client=get_redis(),
            sync_dir=config.SYNC_DIR,
            machine_id=config.MACHINE_ID,
            since=since,
        )
        neo4j_count = result.get("neo4j", {}).get("artifacts", 0)
        duration = time.time() - start
        _log_execution("sync_export", "success", duration, f"{neo4j_count} artifacts")
        logger.info("Scheduled sync export: %d artifacts in %.1fs", neo4j_count, duration)
    except Exception as e:
        duration = time.time() - start
        _log_execution("sync_export", "error", duration, str(e))
        logger.error("Scheduled sync export failed: %s", e)


async def _run_quarantine_purge() -> None:
    """Daily hard-purge of artifacts whose quarantine window has expired.

    Phase 6's ``pkb_quarantine`` sets ``a.purge_after`` (ISO-8601) on
    the :Artifact node when the user soft-deletes with a retention
    window. This job finds rows whose ``purge_after`` is in the past
    and DETACH DELETEs them — same final state as
    ``pkb_artifact_delete(hard=true)`` but auto-triggered by the
    retention window expiring.

    Drops ChromaDB chunks too (collection by collection) so the vector
    side stays consistent with the Neo4j source-of-truth. Best-effort
    on each artifact — a single failure doesn't abort the batch.
    """
    start = time.time()
    purged = 0
    chunk_dropped = 0
    failed = 0
    try:
        from app.db import neo4j as graph
        from app.deps import get_chroma, get_neo4j
        from core.utils.time import utcnow_iso

        now_iso = utcnow_iso()
        driver = get_neo4j()
        chroma = get_chroma()

        # 1. Find candidates whose purge window has elapsed.
        with driver.session() as session:
            result = session.run(
                """
                MATCH (a:Artifact)
                WHERE coalesce(a.archived, false) = true
                  AND a.purge_after IS NOT NULL
                  AND a.purge_after < $now
                RETURN a.id AS id, a.domain AS domain, a.filename AS filename
                LIMIT 200
                """,
                now=now_iso,
            )
            candidates = [dict(r) for r in result]

        # 2. For each, drop chunks + delete the node. graph.delete_artifact
        # already does the DETACH DELETE; we add the ChromaDB sweep.
        for c in candidates:
            artifact_id = c["id"]
            try:
                record = graph.delete_artifact(driver, artifact_id)
                if not record.get("deleted"):
                    continue
                purged += 1
                chunk_ids = record.get("chunk_ids") or []
                if chunk_ids and c.get("domain"):
                    try:
                        coll = chroma.get_collection(
                            name=config.collection_name(c["domain"])
                        )
                        coll.delete(ids=chunk_ids)
                        chunk_dropped += len(chunk_ids)
                    except Exception as exc:  # noqa: BLE001 — collection-gone is a valid post-state during quarantine cleanup
                        log_swallowed_error(__name__, exc)
            except Exception as e:
                failed += 1
                logger.warning("Quarantine purge failed for %s: %s", artifact_id, e)

        duration = time.time() - start
        _log_execution(
            "quarantine_purge", "success", duration,
            f"{purged} purged, {chunk_dropped} chunks dropped, {failed} failed",
        )
        logger.info(
            "Scheduled quarantine purge: %d artifacts removed in %.1fs "
            "(%d chunks, %d failures)",
            purged, duration, chunk_dropped, failed,
        )
    except Exception as e:
        duration = time.time() - start
        _log_execution("quarantine_purge", "error", duration, str(e))
        logger.error("Scheduled quarantine purge failed: %s", e)


async def _run_tombstone_purge() -> None:
    """Weekly purge of expired tombstone records."""
    start = time.time()
    try:
        from app.sync.tombstones import purge_expired
        result = purge_expired(sync_dir=config.SYNC_DIR)
        purged = result.get("purged", 0)
        duration = time.time() - start
        _log_execution("tombstone_purge", "success", duration, f"{purged} purged")
        logger.info("Scheduled tombstone purge: %d expired in %.1fs", purged, duration)
    except Exception as e:
        duration = time.time() - start
        _log_execution("tombstone_purge", "error", duration, str(e))
        logger.error("Scheduled tombstone purge failed: %s", e)


async def _run_folder_scan() -> None:
    """Scheduled folder scan — ingests new files from configured paths."""
    start = time.time()
    try:
        from app.services.folder_scanner import scan_folder

        scan_paths = config.SCAN_PATHS.split(":") if hasattr(config, "SCAN_PATHS") else [config.ARCHIVE_PATH]
        total_ingested = 0
        total_skipped = 0
        total_errored = 0

        for path in scan_paths:
            if not Path(path).is_dir():
                logger.warning(f"Scan path not found: {path}")
                continue
            async for result in scan_folder(
                path,
                min_quality=getattr(config, "SCAN_MIN_QUALITY", 0.4),
                max_file_size_mb=getattr(config, "SCAN_MAX_FILE_SIZE_MB", 50),
            ):
                if result.status == "ingested":
                    total_ingested += 1
                elif result.status in ("duplicate", "low_quality", "skipped"):
                    total_skipped += 1
                elif result.status == "error":
                    total_errored += 1

        duration = time.time() - start
        detail = f"ingested={total_ingested} skipped={total_skipped} errored={total_errored}"
        _log_execution("folder_scan", "success", duration, detail)
        logger.info(f"Folder scan complete: {detail} ({duration:.1f}s)")
    except Exception as e:
        _log_execution("folder_scan", "error", time.time() - start, str(e))
        logger.error(f"Folder scan failed: {e}")


async def _run_ingest_recovery() -> None:
    """Phase O.1 — scan for stale pending Chroma chunks and heal them.

    Enqueues an ``IngestRecoveryJob`` via the processor queue when one is
    available on ``app.state``; otherwise calls the recovery service directly
    so the cron always runs even when the processor is not initialised.

    The job class is imported lazily inside the callback to prevent module-load
    races during startup (the scheduler is started before processor_queue is
    wired onto ``app.state``).
    """
    start = time.time()
    try:
        # Try to enqueue via the processor queue (preferred path).
        try:
            from app.main import app as _app  # type: ignore[import]  # FastAPI app
            queue = getattr(getattr(_app, "state", None), "processor_queue", None)
        except Exception:
            queue = None

        if queue is not None:
            # Lazy import to avoid circular imports at module load time.
            from app.processor.jobs.ingest_recovery import IngestRecoveryJob
            job = IngestRecoveryJob()
            record = job.new_record()
            await queue.enqueue(record)
            logger.debug("ingest_recovery enqueued via processor job_id=%s", record.id)
            duration = time.time() - start
            _log_execution("ingest_recovery", "enqueued", duration)
        else:
            # Fallback: run the recovery service directly.
            from app.services.ingest_recovery import recover_orphan, scan_orphans
            orphans = await scan_orphans()
            committed = purged = deferred = 0
            for orphan in orphans:
                action = await recover_orphan(orphan)
                action_val = str(action.value) if hasattr(action, "value") else str(action)
                if action_val == "committed":
                    committed += 1
                elif action_val == "purged":
                    purged += 1
                else:
                    deferred += 1
            duration = time.time() - start
            detail = f"orphans={len(orphans)} committed={committed} purged={purged} deferred={deferred}"
            _log_execution("ingest_recovery", "success", duration, detail)
            logger.info("ingest_recovery (direct): %s in %.1fs", detail, duration)
    except Exception as e:
        duration = time.time() - start
        _log_execution("ingest_recovery", "error", duration, str(e))
        logger.error("ingest_recovery scheduled job failed: %s", e)


async def _run_wiki_drift_lint() -> None:
    """Phase K2.4 — weekly lint sweep for wiki drift + open contradictions.

    Two checks, both surfaced as enqueued refreshes:

    1. **Unresolved contradictions** — entities with at least one
       :ContradictionFinding edge but a stale or missing summary. Force
       a refresh so the page reflects the disagreement.
    2. **Coverage gaps** — entities with mention_count above the lint
       threshold (default 10) but no summary at all. Their on-ingest
       refresh may have been debounced out or never fired.

    Cheap: bounded by WIKI_DRIFT_LINT_LIMIT (default 50) per run; the
    most-active entities win the slot.
    """
    import os
    start = time.time()
    try:
        from app.deps import get_neo4j  # noqa: PLC0415
        from app.processor.subscribers.wiki_refresh import enqueue_refresh  # noqa: PLC0415

        limit = int(os.environ.get("WIKI_DRIFT_LINT_LIMIT", "50"))
        threshold = int(os.environ.get("WIKI_DRIFT_LINT_MIN_MENTIONS", "10"))
        driver = get_neo4j()
        if driver is None:
            logger.warning("wiki_drift_lint: Neo4j unavailable, skipping")
            return

        def _scan() -> dict[str, list[str]]:
            with driver.session() as session:
                # Unresolved contradictions on entities with stale summaries
                contra_rows = session.run(
                    """
                    MATCH (e:Entity)-[:HAS_CONTRADICTION]->(:ContradictionFinding)
                    WHERE e.summary IS NULL
                       OR e.summary_updated_at IS NULL
                       OR e.summary_updated_at < $cutoff
                    RETURN DISTINCT e.canonical_id AS slug
                    LIMIT $lim
                    """,
                    cutoff=(datetime.now(tz=timezone.utc) - timedelta(hours=24)).isoformat(),
                    lim=limit,
                )
                contradiction_slugs = [r["slug"] for r in contra_rows if r["slug"]]

                # Coverage gaps: high mention, no summary
                gap_rows = session.run(
                    """
                    MATCH (e:Entity)
                    WHERE e.summary IS NULL
                      AND coalesce(e.mention_count, 0) >= $threshold
                    RETURN e.canonical_id AS slug
                    ORDER BY e.mention_count DESC
                    LIMIT $lim
                    """,
                    threshold=threshold,
                    lim=limit,
                )
                gap_slugs = [r["slug"] for r in gap_rows if r["slug"]]

            return {"contradiction": contradiction_slugs, "gap": gap_slugs}

        buckets = await asyncio.to_thread(_scan)
        forced = 0
        debounced = 0
        for slug in buckets["contradiction"]:
            # Contradictions bypass debounce — fresh summary now
            if enqueue_refresh(slug, force=True):
                forced += 1
        for slug in buckets["gap"]:
            if enqueue_refresh(slug, force=False):
                debounced += 1

        duration = time.time() - start
        detail = (
            f"contradictions={len(buckets['contradiction'])} forced={forced} "
            f"gaps={len(buckets['gap'])} enqueued={debounced}"
        )
        _log_execution("wiki_drift_lint", "success", duration, detail)
        logger.info("wiki_drift_lint: %s in %.1fs", detail, duration)
    except Exception as e:
        duration = time.time() - start
        _log_execution("wiki_drift_lint", "error", duration, str(e))
        logger.error("wiki_drift_lint failed: %s", e)


async def _run_wiki_stale_sweep() -> None:
    """Phase K1.4 — nightly wiki refresh sweep.

    Finds entities whose summary is overdue (``next_refresh_due < now()``)
    and enqueues ``WikiRefreshJob`` for the top-``WIKI_STALE_SWEEP_LIMIT``
    ranked by ``mention_count DESC``. Bounded to prevent a fresh corpus
    from melting the LOW priority queue.

    The wiki refresh job itself is the same code path used by the
    ingest-triggered enqueue (Phase K1.3); this sweep is the catch-all
    for entities whose ingest happened before K1.1 shipped, or whose
    debounce window expired without a new event.
    """
    import os
    start = time.time()
    try:
        from app.deps import get_neo4j  # noqa: PLC0415
        from app.processor.subscribers.wiki_refresh import enqueue_refresh  # noqa: PLC0415

        limit = int(os.environ.get("WIKI_STALE_SWEEP_LIMIT", "100"))
        driver = get_neo4j()
        if driver is None:
            logger.warning("wiki_stale_sweep: Neo4j unavailable, skipping")
            return

        # 24h since-last-refresh matches what next_refresh_due represents
        # on the read path (wiki_pages._compute_next_refresh).
        cutoff_iso = (datetime.now(tz=timezone.utc) - timedelta(hours=24)).isoformat()

        def _scan_with_cutoff() -> list[str]:
            with driver.session() as session:
                result = session.run(
                    """
                    MATCH (e:Entity)
                    WHERE e.summary IS NULL
                       OR e.summary_updated_at IS NULL
                       OR e.summary_updated_at < $cutoff
                    RETURN e.canonical_id AS slug
                    ORDER BY coalesce(e.mention_count, 0) DESC
                    LIMIT $lim
                    """,
                    cutoff=cutoff_iso,
                    lim=limit,
                )
                return [row["slug"] for row in result if row["slug"]]

        slugs = await asyncio.to_thread(_scan_with_cutoff)
        enqueued = 0
        for slug in slugs:
            # Force=False so per-entity debounce still applies; if the
            # subscriber already enqueued for this slug in the last
            # WIKI_REFRESH_DEBOUNCE_TTL seconds, the sweep skips it.
            if enqueue_refresh(slug, force=False):
                enqueued += 1

        duration = time.time() - start
        detail = f"candidates={len(slugs)} enqueued={enqueued} limit={limit}"
        _log_execution("wiki_stale_sweep", "success", duration, detail)
        logger.info("wiki_stale_sweep: %s in %.1fs", detail, duration)
    except Exception as e:
        duration = time.time() - start
        _log_execution("wiki_stale_sweep", "error", duration, str(e))
        logger.error("wiki_stale_sweep failed: %s", e)


async def _run_config_recommender() -> None:
    """Cycle 3.2 — periodic evaluation of the recommendation registry.

    Mirrors :func:`_run_ingest_recovery` exactly: try enqueueing via
    the processor queue (preferred path), fall back to running the
    body directly when the queue isn't on ``app.state`` yet (early
    startup / lightweight mode).
    """
    start = time.time()
    try:
        try:
            from app.main import app as _app  # type: ignore[import]
            queue = getattr(getattr(_app, "state", None), "processor_queue", None)
        except Exception:  # noqa: BLE001 — queue probe is best-effort
            queue = None

        if queue is not None:
            from app.processor.jobs.config_recommender import ConfigRecommenderJob
            job = ConfigRecommenderJob()
            record = job.new_record()
            await queue.enqueue(record)
            logger.debug(
                "config_recommender enqueued via processor job_id=%s", record.id,
            )
            duration = time.time() - start
            _log_execution("config_recommender", "enqueued", duration)
        else:
            from app.deps import get_neo4j, get_redis
            from app.processor.jobs.config_recommender import run_recommender_sync

            try:
                driver = get_neo4j()
            except Exception:  # noqa: BLE001 — direct-call fallback only
                driver = None
            try:
                redis_client = get_redis()
            except Exception:  # noqa: BLE001 — direct-call fallback only
                redis_client = None
            meta = run_recommender_sync(driver, redis_client)
            duration = time.time() - start
            detail = (
                f"corpus={meta['corpus_size']} "
                f"writes={meta['recommendations_written']}"
            )
            _log_execution("config_recommender", "success", duration, detail)
            logger.info(
                "config_recommender (direct): %s in %.1fs", detail, duration,
            )
    except Exception as e:  # noqa: BLE001 — scheduler error surface
        duration = time.time() - start
        _log_execution("config_recommender", "error", duration, str(e))
        logger.error("config_recommender scheduled job failed: %s", e)


async def _run_k_program_metrics() -> None:
    """Phase S4 of the unified GA program — daily K-program metrics
    snapshot. Runs ``scripts/k_program_metrics.py --cron`` in-process so
    the output lands in ``tasks/<monday>-k-program-metrics.md`` and the
    operator inherits 14 days of timestamped rows without leaving the
    container.

    The script is process-isolated by design — it opens its own Neo4j
    and Redis drivers from the same env the MCP picked up. Running it
    in-process here keeps the scheduling consistent with the other
    K-program crons (wiki_stale_sweep, wiki_drift_lint).
    """
    start = time.time()
    try:
        import subprocess
        from pathlib import Path

        # Walk up from this file to the repo root (src/mcp/app/scheduler.py).
        repo_root = Path(__file__).resolve().parents[3]
        script = repo_root / "scripts" / "k_program_metrics.py"
        if not script.exists():
            logger.warning(
                "k_program_metrics script not found at %s — skip", script,
            )
            return
        # Use the same Python the container is running.
        import sys as _sys

        result = subprocess.run(
            [_sys.executable, str(script), "--cron"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        duration = time.time() - start
        if result.returncode != 0:
            _log_execution(
                "k_program_metrics",
                "error",
                duration,
                result.stderr.strip().split("\n")[-1][:200],
            )
            logger.error(
                "k_program_metrics scheduled job failed (rc=%d): %s",
                result.returncode,
                result.stderr.strip()[-500:],
            )
            return
        # Successful run; stderr carries the human-readable path emit
        detail = result.stderr.strip().split("\n")[-1][:200] if result.stderr else ""
        _log_execution("k_program_metrics", "success", duration, detail)
        logger.info("k_program_metrics snapshot complete in %.1fs", duration)
    except Exception as e:  # noqa: BLE001 — scheduler error surface
        duration = time.time() - start
        _log_execution("k_program_metrics", "error", duration, str(e))
        logger.error("k_program_metrics scheduled job failed: %s", e)


def start_scheduler() -> AsyncIOScheduler:
    """Create and start the scheduler with configured jobs."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = AsyncIOScheduler()

    _scheduler.add_job(
        _run_rectify,
        CronTrigger.from_crontab(config.SCHEDULE_RECTIFY),
        id="rectify",
        name="Daily rectification",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_health_check,
        CronTrigger.from_crontab(config.SCHEDULE_HEALTH_CHECK),
        id="health_check",
        name="Health check",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_stale_detection,
        CronTrigger.from_crontab(config.SCHEDULE_STALE_DETECTION),
        id="stale_detection",
        name="Weekly stale detection",
        replace_existing=True,
    )

    # Phase K Day 1 — daily digest (default 7 AM UTC when
    # CERID_DAILY_DIGEST_ENABLED is set + daily_digest feature on).
    if getattr(config, "SCHEDULE_DAILY_DIGEST", ""):
        _scheduler.add_job(
            _run_daily_digest,
            CronTrigger.from_crontab(config.SCHEDULE_DAILY_DIGEST),
            id="daily_digest",
            name="Daily digest (Pro)",
            replace_existing=True,
            max_instances=1,
        )

    # Phase J Day 2 — inbox triage (every 15 min by default when
    # CERID_INBOX_TRIAGE_ENABLED is set + inbox_triage feature on).
    # Empty SCHEDULE_INBOX_TRIAGE disables the cron entirely.
    if getattr(config, "SCHEDULE_INBOX_TRIAGE", ""):
        _scheduler.add_job(
            _run_inbox_triage,
            CronTrigger.from_crontab(config.SCHEDULE_INBOX_TRIAGE),
            id="inbox_triage",
            name="Inbox triage (Pro)",
            replace_existing=True,
            max_instances=1,  # block overlapping runs (LLM cost)
        )

    # Sync export (optional — empty SCHEDULE_SYNC_EXPORT disables)
    if getattr(config, "SCHEDULE_SYNC_EXPORT", ""):
        _scheduler.add_job(
            _run_sync_export,
            CronTrigger.from_crontab(config.SCHEDULE_SYNC_EXPORT),
            id="sync_export",
            name="Incremental sync export",
            replace_existing=True,
        )

    # Weekly tombstone purge (always active — negligible cost)
    _scheduler.add_job(
        _run_tombstone_purge,
        CronTrigger.from_crontab("0 5 * * 0"),  # Sunday 5 AM
        id="tombstone_purge",
        name="Weekly tombstone purge",
        replace_existing=True,
    )

    # Phase K1.4 — nightly wiki refresh sweep (3 AM local). Catches
    # entities whose summaries are overdue (next_refresh_due elapsed)
    # and weren't picked up by the on-ingest subscriber (Phase K1.3).
    # Bounded to WIKI_STALE_SWEEP_LIMIT (default 100) per night.
    _scheduler.add_job(
        _run_wiki_stale_sweep,
        CronTrigger.from_crontab(
            getattr(config, "SCHEDULE_WIKI_STALE_SWEEP", "0 3 * * *"),
        ),
        id="wiki_stale_sweep",
        name="Wiki refresh sweep (stale entities)",
        replace_existing=True,
        max_instances=1,
    )

    # Phase K2.4 — weekly wiki drift lint (Sunday 4 AM, after the
    # tombstone purge). Two-pass scan: (a) entities with open
    # contradictions on stale summaries (force refresh), (b) high-
    # mention entities with no summary (debounced refresh).
    _scheduler.add_job(
        _run_wiki_drift_lint,
        CronTrigger.from_crontab(
            getattr(config, "SCHEDULE_WIKI_DRIFT_LINT", "0 4 * * 0"),
        ),
        id="wiki_drift_lint",
        name="Wiki drift lint (contradictions + coverage gaps)",
        replace_existing=True,
        max_instances=1,
    )

    # Cycle 3.2 — config recommender: scan corpus + flag state every
    # 6 h and refresh cerid:recommendations in Redis.  LOW priority,
    # zero LLM cost; mirrors the ingest_recovery enqueue-or-direct
    # fallback so it always runs even before processor_queue is wired.
    _scheduler.add_job(
        _run_config_recommender,
        CronTrigger.from_crontab(
            getattr(config, "SCHEDULE_CONFIG_RECOMMENDER", "0 */6 * * *"),
        ),
        id="config_recommender",
        name="Adaptive config recommender",
        replace_existing=True,
        max_instances=1,
    )

    # Phase S4 of the unified GA program — K-program metrics snapshot.
    # Runs scripts/k_program_metrics.py --cron once per day so the
    # 14-day soak window captures six metrics (wiki coverage, p95
    # staleness, faithfulness, chunks-per-answer, memory→entity
    # linkage, contradiction p95) into tasks/<monday>-k-program-metrics.md.
    # Empty SCHEDULE_K_PROGRAM_METRICS disables the in-process cron
    # (operator may prefer host-side launchd / system cron).
    if getattr(config, "SCHEDULE_K_PROGRAM_METRICS", ""):
        _scheduler.add_job(
            _run_k_program_metrics,
            CronTrigger.from_crontab(config.SCHEDULE_K_PROGRAM_METRICS),
            id="k_program_metrics",
            name="K-program metrics snapshot (S4 soak)",
            replace_existing=True,
            max_instances=1,
        )

    # v0.95.1 Phase 6 follow-up — quarantine auto-purge: daily sweep of
    # :Artifact nodes whose purge_after has elapsed. Drops the Neo4j
    # node + ChromaDB chunks. Soft path is pkb_quarantine; this job is
    # what eventually hard-deletes.
    _scheduler.add_job(
        _run_quarantine_purge,
        CronTrigger.from_crontab(
            getattr(config, "SCHEDULE_QUARANTINE_PURGE", "0 3 * * *"),
        ),
        id="quarantine_purge",
        name="Quarantine auto-purge (retention-window expiry)",
        replace_existing=True,
        max_instances=1,
    )

    # Phase O.1 — ingest recovery: scan for stale pending Chroma chunks every
    # 60 s and roll them forward or purge.  Uses a processor_queue if one is
    # available on app.state; falls back to direct service call otherwise.
    _scheduler.add_job(
        _run_ingest_recovery,
        "interval",
        seconds=60,
        id="ingest_recovery",
        name="Ingest orphan recovery",
        replace_existing=True,
        max_instances=1,
    )

    # Invoke any registered internal-build hooks (no-op in the public build;
    # set by the module-level bootstrap block at the bottom of this file).
    for hook in _post_setup_hooks:
        hook(_scheduler)

    # Folder scan (opt-in — empty SCHEDULE_FOLDER_SCAN disables)
    scan_cron = getattr(config, "SCHEDULE_FOLDER_SCAN", "")
    if scan_cron:
        _scheduler.add_job(
            _run_folder_scan,
            CronTrigger.from_crontab(scan_cron),
            id="folder_scan",
            name="Autonomous folder scan",
            replace_existing=True,
        )
        logger.info(f"Folder scan scheduled: {scan_cron}")

    _scheduler.start()
    logger.info("Scheduler started with %d jobs", len(_scheduler.get_jobs()))
    return _scheduler


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
        _scheduler = None


def get_scheduler() -> AsyncIOScheduler | None:
    """Get the current scheduler instance."""
    return _scheduler


def get_job_status() -> dict[str, Any]:
    """Return status of all scheduled jobs."""
    if _scheduler is None:
        return {"status": "not_running", "jobs": []}
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
            "trigger": str(job.trigger),
        })
    return {"status": "running", "jobs": jobs}
