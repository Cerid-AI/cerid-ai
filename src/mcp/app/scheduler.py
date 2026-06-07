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
from typing import TYPE_CHECKING, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

if TYPE_CHECKING:
    from core.ingest.sources.kinds import SourceKind

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


async def _run_retention_enforce() -> None:
    """Nightly per-source retention enforcement. Walks every
    (:Source) and applies its ``retention_policy``, purging artifacts
    that fall outside the policy window.
    """
    start = time.time()
    try:
        from app.services.retention import enforce_all_retention

        summary = enforce_all_retention()
        duration = time.time() - start
        _log_execution(
            "retention_enforce",
            "success",
            duration,
            f"purged={summary.get('total_purged', 0)}",
        )
        logger.info(
            "retention_enforce complete: %d artifacts purged in %.1fs",
            summary.get("total_purged", 0),
            duration,
        )
    except Exception as e:  # noqa: BLE001 — scheduler error surface
        duration = time.time() - start
        _log_execution("retention_enforce", "error", duration, str(e))
        logger.error("retention_enforce scheduled job failed: %s", e)


async def _run_knowledge_stats_snapshot() -> None:
    """Daily Knowledge Stats snapshot for sparkline rendering.
    Writes one :KnowledgeStatsSnapshot node per day; idempotent
    re-runs overwrite the same day's payload via MERGE.
    """
    start = time.time()
    try:
        from app.db.neo4j.stats import fetch_current_stats, write_stats_snapshot
        from app.deps import get_neo4j

        driver = get_neo4j()
        snapshot = fetch_current_stats(driver)
        write_stats_snapshot(driver, snapshot)
        duration = time.time() - start
        artifacts = snapshot.get("nodes", {}).get("artifacts", 0)
        _log_execution(
            "knowledge_stats_snapshot",
            "success",
            duration,
            f"artifacts={artifacts}",
        )
        logger.info("knowledge_stats_snapshot complete: artifacts=%d in %.1fs", artifacts, duration)
    except Exception as e:  # noqa: BLE001 — scheduler error surface
        duration = time.time() - start
        _log_execution("knowledge_stats_snapshot", "error", duration, str(e))
        logger.error("knowledge_stats_snapshot scheduled job failed: %s", e)


async def _run_model_auto_update() -> None:
    """Adopt the latest in-family model for every role from the OpenRouter
    catalog (gated by MODEL_AUTO_UPDATE_ENABLED). Same-family + must-exist
    bounds the drift; the change applies on the next Bifrost restart."""
    start = time.time()
    try:
        from app.routers.models import apply_latest_assignments

        outcome = await apply_latest_assignments()
        applied = outcome.get("applied", [])
        duration = time.time() - start
        _log_execution("model_auto_update", "success", duration, f"applied={len(applied)}")
        if applied:
            logger.info(
                "model_auto_update: %d role(s) updated to latest in-family", len(applied)
            )
    except Exception as e:  # noqa: BLE001 — scheduler error surface
        duration = time.time() - start
        _log_execution("model_auto_update", "error", duration, str(e))
        logger.error("model_auto_update scheduled job failed: %s", e)


async def _run_community_refresh() -> None:
    """Weekly Leiden community re-detection + summary refresh (graph/atlas).

    Re-clusters the entity graph (GDS Leiden — cheap), rewrites Community nodes,
    IN_COMMUNITY edges, and the scalar Entity.community_id the renderers read,
    then summarizes communities lacking a summary (skip-existing bounds the
    single-GPU LLM cost). Gated via SCHEDULE_COMMUNITY_REFRESH.
    """
    start = time.time()
    try:
        from app.db.neo4j.community_detection import detect_communities
        from app.db.neo4j.community_summaries import summarize_communities
        from app.deps import get_chroma, get_neo4j

        driver = get_neo4j()
        if driver is None:
            _log_execution("community_refresh", "skipped", time.time() - start, "neo4j unavailable")
            return
        det = await asyncio.to_thread(detect_communities, driver)
        _summary_cap = int(getattr(config, "COMMUNITY_SUMMARY_MAX_PER_RUN", 200)) or None
        summ = await summarize_communities(driver, get_chroma(), max_communities=_summary_cap)
        duration = time.time() - start
        detail = (
            f"edges={det.get('edges', det.get('skipped', '?'))} "
            f"summarised={summ.get('summarised', '?')}"
        )
        _log_execution("community_refresh", "success", duration, detail)
        logger.info("community_refresh: %s in %.1fs", detail, duration)
    except Exception as e:  # noqa: BLE001 — scheduler error surface
        duration = time.time() - start
        _log_execution("community_refresh", "error", duration, str(e))
        logger.error("community_refresh scheduled job failed: %s", e)


async def _run_compute_umap_3d() -> None:
    """Nightly Constellation 3D-coordinate compute.

    Enqueues ComputeUmap3DJob via the processor queue when available, else runs
    it directly (mirrors _run_ingest_recovery). Emits the deterministic fallback
    layout today (no umap dependency); coords key off community_id so the
    Constellation/atlas view renders. Gated via SCHEDULE_COMPUTE_UMAP_3D.
    """
    start = time.time()
    try:
        from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob

        try:
            from app.main import app as _app  # type: ignore[import]
            queue = getattr(getattr(_app, "state", None), "processor_queue", None)
        except Exception:
            queue = None

        job = ComputeUmap3DJob()
        if queue is not None:
            record = job.new_record()
            await queue.enqueue(record)
            _log_execution("compute_umap_3d", "enqueued", time.time() - start)
        else:
            async def _noop(_pct: float) -> None:
                return None

            await job.run(_noop)
            _log_execution("compute_umap_3d", "success", time.time() - start)
    except Exception as e:  # noqa: BLE001 — scheduler error surface
        duration = time.time() - start
        _log_execution("compute_umap_3d", "error", duration, str(e))
        logger.error("compute_umap_3d scheduled job failed: %s", e)


async def _run_memory_consolidation_sweep() -> None:
    """Weekly SAFE memory archival sweep — archival only, no LLM re-abstraction.

    Calls archive_old_memories (cheap, no LLM): marks old conversation memories
    archived/deprioritized. Per research, continuous aggressive LLM consolidation
    degrades utility below baseline, so supersession stays write-time + explicit;
    this scheduled path does only the safe archival. Gated via
    SCHEDULE_MEMORY_CONSOLIDATION.
    """
    start = time.time()
    try:
        from app.deps import get_neo4j
        from core.agents.memory import archive_old_memories

        driver = get_neo4j()
        if driver is None:
            _log_execution(
                "memory_consolidation_sweep", "skipped", time.time() - start, "neo4j unavailable",
            )
            return
        res = await archive_old_memories(driver)
        duration = time.time() - start
        detail = f"archived={res.get('archived_count', '?')}"
        _log_execution("memory_consolidation_sweep", "success", duration, detail)
        logger.info("memory_consolidation_sweep: %s in %.1fs", detail, duration)
    except Exception as e:  # noqa: BLE001 — scheduler error surface
        duration = time.time() - start
        _log_execution("memory_consolidation_sweep", "error", duration, str(e))
        logger.error("memory_consolidation_sweep scheduled job failed: %s", e)


async def _run_webhook_drain() -> None:
    """Consume cerid:webhook_inbox:* and route entries into the KB.

    The webhook receiver (POST /sdk/v1/ingest/webhook/{token}) verifies the
    token + HMAC, normalizes via the adapter recipe, rpush'es onto
    cerid:webhook_inbox:{source_id}, and returns 202 — but nothing consumed that
    list, so every webhook payload was accepted then stranded. This is that
    consumer. Per entry: LPOP → ingest_content (dedup-safe) → on failure move to
    cerid:webhook_deadletter:{source_id} so one poison entry can't loop forever
    or stall the source. Bounded per run. Gated via SCHEDULE_WEBHOOK_DRAIN.
    """
    import json as _json

    start = time.time()
    try:
        from app.services.ingestion import ingest_content

        rc = get_redis()
        if rc is None:
            _log_execution("webhook_drain", "skipped", time.time() - start, "redis unavailable")
            return
        driver = get_neo4j()
        max_per_run = int(getattr(config, "WEBHOOK_DRAIN_MAX_PER_RUN", 200))
        keys = [
            k.decode() if isinstance(k, bytes) else k
            for k in rc.scan_iter(match="cerid:webhook_inbox:*", count=100)
        ]
        ingested = failed = 0
        for key in keys:
            source_id = key.rsplit(":", 1)[-1]
            # Resolve the source's domain once per key (default general).
            domain = "general"
            if driver is not None:
                try:
                    with driver.session() as _s:
                        _row = _s.run(
                            "MATCH (src:Source {id: $id}) "
                            "RETURN coalesce(src.domain, 'general') AS d",
                            id=source_id,
                        ).single()
                        if _row and _row.get("d"):
                            domain = _row["d"]
                except Exception as exc:  # noqa: BLE001 — domain lookup best-effort
                    log_swallowed_error("app.scheduler.webhook_drain.domain", exc)
            n = 0
            while n < max_per_run:
                raw = rc.lpop(key)
                if raw is None:
                    break
                n += 1
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                try:
                    entry = _json.loads(raw)
                    arts = entry.get("normalized") or [
                        {"content": _json.dumps(entry.get("payload", {})), "title": "webhook payload"}
                    ]
                    for art in arts:
                        content = (art.get("content") or "").strip()
                        if not content:
                            continue
                        meta = {
                            "source_id": source_id,
                            "source_type": "webhook",
                            "title": art.get("title", ""),
                            "url": art.get("url", ""),
                            "provider": art.get("provider", ""),
                            "received_at": entry.get("received_at", ""),
                        }
                        # ingest_content is sync + dedup-safe (content-hash), so a
                        # re-delivery is idempotent.
                        await asyncio.to_thread(
                            ingest_content, content=content, domain=domain, metadata=meta,
                        )
                        ingested += 1
                except Exception as exc:  # noqa: BLE001 — isolate poison entries
                    failed += 1
                    try:
                        rc.rpush(f"cerid:webhook_deadletter:{source_id}", raw)
                    except Exception as dlx:  # noqa: BLE001
                        log_swallowed_error("app.scheduler.webhook_drain.deadletter", dlx)
                    log_swallowed_error("app.scheduler.webhook_drain.ingest", exc)
        duration = time.time() - start
        detail = f"keys={len(keys)} ingested={ingested} failed={failed}"
        _log_execution("webhook_drain", "success", duration, detail)
        if ingested or failed:
            logger.info("webhook_drain: %s in %.1fs", detail, duration)
    except Exception as e:  # noqa: BLE001 — scheduler error surface
        duration = time.time() - start
        _log_execution("webhook_drain", "error", duration, str(e))
        logger.error("webhook_drain scheduled job failed: %s", e)


_POLLABLE_KINDS: tuple[SourceKind, ...] = (
    "rss",
    "url_watch",
    "apple_mail",
    "apple_reminders",
)


async def _run_source_poll() -> None:
    """Drive SourceConnector.fetch_since for active pollable sources on a cadence.

    The missing spine of incremental connector ingestion: for each connected
    rss/url_watch source it reads the sync cursor, async-iterates fetch_since
    (which fetches the feed, ingests each new entry via the DI ingest sink, and
    yields a per-artifact event), and persists event.cursor_after after EACH
    ingested artifact — so the cursor only advances past committed work
    (crash-safe at-least-once resume; ingest_content dedups re-delivery).
    Bounded per source. Gated via SCHEDULE_SOURCE_POLL.
    """
    start = time.time()
    try:
        import core.ingest.sources.connectors as _conns  # noqa: F401 — side-effect: registers connectors
        from app.db.neo4j.sources import list_sources
        from app.services.sync_cursor import get_cursor, set_cursor
        from core.ingest.sources.registry import get_connector

        rc = get_redis()
        driver = get_neo4j()
        if driver is None:
            _log_execution("source_poll", "skipped", time.time() - start, "neo4j unavailable")
            return
        max_arts = int(getattr(config, "SOURCE_POLL_MAX_ARTIFACTS_PER_SOURCE", 50))
        polled = ingested = 0
        for kind in _POLLABLE_KINDS:
            connector = get_connector(kind)
            if connector is None:
                continue
            for src in list_sources(driver, kind=kind):
                status = src.get("status")
                if status and status != "connected":
                    continue  # skip paused / error / needs_auth
                source_id = src.get("id")
                if not source_id:
                    continue
                cfg = dict(src.get("config") or {})
                cfg.setdefault("domain", src.get("domain", "general"))
                cursor = get_cursor(rc, driver, source_id)
                polled += 1
                n = 0
                try:
                    async for event in connector.fetch_since(source_id, cursor, cfg):
                        # Persist after each committed artifact — crash-safe.
                        set_cursor(rc, driver, source_id, event.cursor_after)
                        ingested += 1
                        n += 1
                        if n >= max_arts:
                            break
                except Exception as exc:  # noqa: BLE001 — one source's failure mustn't stop the sweep
                    log_swallowed_error("app.scheduler.source_poll.fetch", exc)
        duration = time.time() - start
        detail = f"polled={polled} ingested={ingested}"
        _log_execution("source_poll", "success", duration, detail)
        if ingested:
            logger.info("source_poll: %s in %.1fs", detail, duration)
    except Exception as e:  # noqa: BLE001 — scheduler error surface
        duration = time.time() - start
        _log_execution("source_poll", "error", duration, str(e))
        logger.error("source_poll scheduled job failed: %s", e)


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

    # Weekly auto-adoption of the latest in-family model per role from the
    # OpenRouter catalog. Gated by MODEL_AUTO_UPDATE_ENABLED (default on).
    if getattr(config, "MODEL_AUTO_UPDATE_ENABLED", True):
        _scheduler.add_job(
            _run_model_auto_update,
            CronTrigger.from_crontab(
                getattr(config, "SCHEDULE_MODEL_AUTO_UPDATE", "0 6 * * 1"),
            ),
            id="model_auto_update",
            name="Auto-update models to latest in-family",
            replace_existing=True,
            max_instances=1,
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

    # Graph/atlas freshness — weekly Leiden re-detection + summaries. Gated;
    # empty SCHEDULE_COMMUNITY_REFRESH disables. Writes Entity.community_id so
    # the graph renderers light up; skip-existing summaries bound GPU cost.
    if getattr(config, "SCHEDULE_COMMUNITY_REFRESH", "0 2 * * 0"):
        _scheduler.add_job(
            _run_community_refresh,
            CronTrigger.from_crontab(
                getattr(config, "SCHEDULE_COMMUNITY_REFRESH", "0 2 * * 0"),
            ),
            id="community_refresh",
            name="Leiden community re-detection + summaries",
            replace_existing=True,
            max_instances=1,
        )

    # Constellation 3D coords — nightly. Gated; empty SCHEDULE_COMPUTE_UMAP_3D
    # disables. Fallback layout today (no umap dep); keyed off community_id.
    if getattr(config, "SCHEDULE_COMPUTE_UMAP_3D", "30 3 * * *"):
        _scheduler.add_job(
            _run_compute_umap_3d,
            CronTrigger.from_crontab(
                getattr(config, "SCHEDULE_COMPUTE_UMAP_3D", "30 3 * * *"),
            ),
            id="compute_umap_3d",
            name="Constellation 3D coordinate compute",
            replace_existing=True,
            max_instances=1,
        )

    # Memory archival sweep — weekly, SAFE (archival only, no LLM). Gated;
    # empty SCHEDULE_MEMORY_CONSOLIDATION disables.
    if getattr(config, "SCHEDULE_MEMORY_CONSOLIDATION", "0 5 * * 0"):
        _scheduler.add_job(
            _run_memory_consolidation_sweep,
            CronTrigger.from_crontab(
                getattr(config, "SCHEDULE_MEMORY_CONSOLIDATION", "0 5 * * 0"),
            ),
            id="memory_consolidation_sweep",
            name="Memory archival sweep (safe consolidation)",
            replace_existing=True,
            max_instances=1,
        )

    # Webhook-inbox drain — consume cerid:webhook_inbox:* into the KB (the
    # receiver returns 202 + enqueues; this is the missing consumer). Every 2
    # min by default. Gated; empty SCHEDULE_WEBHOOK_DRAIN disables.
    if getattr(config, "SCHEDULE_WEBHOOK_DRAIN", "*/2 * * * *"):
        _scheduler.add_job(
            _run_webhook_drain,
            CronTrigger.from_crontab(
                getattr(config, "SCHEDULE_WEBHOOK_DRAIN", "*/2 * * * *"),
            ),
            id="webhook_drain",
            name="Webhook inbox drain",
            replace_existing=True,
            max_instances=1,
        )

    # Connector polling — drive fetch_since for active rss/url_watch sources,
    # advancing the cursor only past committed artifacts (crash-safe). Every 15
    # min by default. Gated; empty SCHEDULE_SOURCE_POLL disables.
    if getattr(config, "SCHEDULE_SOURCE_POLL", "*/15 * * * *"):
        _scheduler.add_job(
            _run_source_poll,
            CronTrigger.from_crontab(
                getattr(config, "SCHEDULE_SOURCE_POLL", "*/15 * * * *"),
            ),
            id="source_poll",
            name="Connector polling (fetch_since)",
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

    # Daily Knowledge Stats snapshot for the Sources hero sparklines.
    # Default midnight UTC; empty SCHEDULE_KNOWLEDGE_STATS_SNAPSHOT disables.
    if getattr(config, "SCHEDULE_KNOWLEDGE_STATS_SNAPSHOT", "0 0 * * *"):
        _scheduler.add_job(
            _run_knowledge_stats_snapshot,
            CronTrigger.from_crontab(
                getattr(config, "SCHEDULE_KNOWLEDGE_STATS_SNAPSHOT", "0 0 * * *"),
            ),
            id="knowledge_stats_snapshot",
            name="Knowledge Stats daily snapshot",
            replace_existing=True,
            max_instances=1,
        )

    # Nightly per-source retention enforcement. Default 2 AM UTC;
    # empty SCHEDULE_RETENTION_ENFORCE disables.
    if getattr(config, "SCHEDULE_RETENTION_ENFORCE", "0 2 * * *"):
        _scheduler.add_job(
            _run_retention_enforce,
            CronTrigger.from_crontab(
                getattr(config, "SCHEDULE_RETENTION_ENFORCE", "0 2 * * *"),
            ),
            id="retention_enforce",
            name="Per-source retention enforcement",
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
