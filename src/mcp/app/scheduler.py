# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Scheduled Maintenance Engine.

Runs background tasks on configurable schedules using APScheduler.
Execution results are logged to Redis for monitoring.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from app.deps import get_chroma, get_neo4j, get_redis
from core.utils.cache import log_event
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
        from app.deps import get_chroma, get_neo4j
        from app.db import neo4j as graph
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
                    except Exception:
                        # Collection missing or chunks already gone —
                        # the Neo4j node is already deleted, so soldier on.
                        pass
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
