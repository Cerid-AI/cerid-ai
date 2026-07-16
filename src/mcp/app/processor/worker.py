# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""ProcessorWorker — drains the job queue and executes jobs.

Architecture
------------
``ProcessorWorker`` owns ``concurrency`` async worker-loop tasks.  Each
task polls the queue using the priority order from ``core.processor.priority``
and executes jobs via their ``BaseJob.run`` interface.

Throttling
----------
System-load throttling is based on ``os.getloadavg()[0]``.  The default
ceiling is ``effective_cpus × 0.7`` where the effective CPU count comes
from the cgroup v2 quota (``/sys/fs/cgroup/cpu.max``) when readable,
falling back to ``os.cpu_count()``.  Callers may override via the
``load_ceiling`` constructor parameter.  On Windows (no getloadavg)
throttling is disabled.

Retry
-----
On job failure the worker bumps ``retry_count`` and re-enqueues the job
up to ``max_retries`` (default 3) times.  Re-enqueue creates a new
``JobRecord`` so the original failure record stays in the recent list
with its error message intact.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.processor.model_policy import resolve_job_model
from config import settings
from core.processor import cost as cost_estimator
from core.processor.job import BaseJob, JobRecord, JobResult, JobState
from core.processor.mode import processor_is_disabled, resolve_processor_mode
from core.processor.priority import priority_order
from core.utils.internal_llm import llm_call_override
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.processor.worker")

# Default maximum retries before a job is permanently failed.
_DEFAULT_MAX_RETRIES = 3
# Graceful-stop drain timeout in seconds.
_STOP_TIMEOUT_S = 30.0
# cgroup v2 CPU quota file: "<quota_us> <period_us>" or "max <period_us>".
_CGROUP_CPU_MAX_PATH = "/sys/fs/cgroup/cpu.max"


def _effective_cpu_count(cpu_max_path: str = _CGROUP_CPU_MAX_PATH) -> float:
    """Return the CPU count the process can actually use.

    ``os.cpu_count()`` reports the HOST's cores even inside a CPU-quota'd
    container (observed live: ceiling 16.8 in a 2-CPU cgroup, so the load
    throttle could mathematically never engage and the container OOM'd).
    Prefer the cgroup v2 quota when readable; ``"max"`` (no quota) and any
    read/parse failure fall back to ``os.cpu_count()``.
    """
    fallback = float(os.cpu_count() or 1)
    try:
        raw = Path(cpu_max_path).read_text().strip()
    except OSError:
        return fallback
    parts = raw.split()
    if parts and parts[0] == "max":
        return fallback
    try:
        quota_raw, period_raw = parts
        quota = float(quota_raw)
        period = float(period_raw)
    except ValueError:
        return fallback
    if quota <= 0 or period <= 0:
        return fallback
    return quota / period


class ProcessorWorker:
    """Async worker that drains a ``JobQueueProtocol``-compatible queue.

    Parameters
    ----------
    queue
        Any object that satisfies ``JobQueueProtocol`` (e.g.
        ``RedisJobQueue``).  Kept as ``Any`` here because ``core/`` defines
        the Protocol and the worker lives in ``app/``.
    job_registry
        Map from ``job_type`` string to the concrete ``BaseJob`` subclass.
        Build via :func:`build_default_registry`.
    concurrency
        Number of parallel worker-loop tasks to spawn.
    poll_interval
        Seconds to sleep between queue polls when idle.
    load_ceiling
        Override the system-load throttle ceiling.  Defaults to
        ``effective_cpus × 0.7`` (cgroup-quota aware).  ``None`` forces
        the default.
    redis_client
        Optional Redis client for metrics recording.  If ``None``, metrics
        writes are skipped silently.
    max_retries
        Maximum retry attempts for a failing job before permanent failure.
    """

    def __init__(
        self,
        queue: Any,
        job_registry: dict[str, type[BaseJob]],
        *,
        concurrency: int = 2,
        poll_interval: float = 0.5,
        load_ceiling: float | None = None,
        redis_client: Any | None = None,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        self._queue = queue
        self._job_registry = job_registry
        self._concurrency = concurrency
        self._poll_interval = poll_interval
        self._redis_client = redis_client
        self._max_retries = max_retries

        # Compute default load ceiling once at init
        try:
            cpu = _effective_cpu_count()
        except Exception as exc:
            log_swallowed_error('app.processor.worker', exc)
            cpu = 1.0
        self._load_ceiling = load_ceiling if load_ceiling is not None else cpu * 0.7

        self._stop_flag = False
        self._tasks: list[asyncio.Task[None]] = []

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """Spawn worker-loop tasks.  Idempotent — safe to call multiple times."""
        if self._tasks:
            return
        self._stop_flag = False
        # A worker start means no prior worker survives: requeue anything a
        # dead worker stranded in the running set (otherwise those ghosts
        # block duplicate-enqueue collapse forever). Best-effort — a broken
        # queue must not stop the worker from starting.
        recover = getattr(self._queue, "recover_orphaned_running", None)
        if recover is not None:
            try:
                recovered = await recover()
                if recovered:
                    logger.info(
                        "processor.recovered %d orphaned running job(s): %s",
                        len(recovered), ", ".join(recovered[:5]),
                    )
            except Exception as exc:  # noqa: BLE001
                log_swallowed_error("app.processor.worker", exc)
        for i in range(self._concurrency):
            task = asyncio.create_task(
                self._worker_loop(i), name=f"processor-worker-{i}"
            )
            self._tasks.append(task)
        logger.info(
            "ProcessorWorker started (%d tasks, load_ceiling=%.1f)",
            self._concurrency,
            self._load_ceiling,
        )

    async def stop(self) -> None:
        """Signal all worker tasks to stop and await their completion."""
        self._stop_flag = True
        if not self._tasks:
            return

        # Give in-flight tasks a chance to finish gracefully.
        done, pending = await asyncio.wait(
            self._tasks, timeout=_STOP_TIMEOUT_S
        )

        for task in pending:
            logger.warning(
                "ProcessorWorker: task %s did not stop in %.0fs — cancelling",
                task.get_name(),
                _STOP_TIMEOUT_S,
            )
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception) as exc:
                log_swallowed_error('app.processor.worker', exc)
                pass

        self._tasks.clear()
        logger.info("ProcessorWorker stopped")

    # -----------------------------------------------------------------------
    # Worker loop
    # -----------------------------------------------------------------------

    async def _worker_loop(self, worker_id: int) -> None:
        """Main polling loop for a single worker."""
        while not self._stop_flag:
            try:
                # Disabled-mode check — read live each tick so a runtime PATCH
                # to PROCESSOR_MODE takes effect without a worker restart.
                if processor_is_disabled(resolve_processor_mode(settings.PROCESSOR_MODE)):
                    await asyncio.sleep(self._poll_interval)
                    continue

                # Throttle check — backs off when system load is high.
                if self._is_throttled():
                    if self._redis_client is not None:
                        from app.processor.metrics import record_throttled

                        await record_throttled(self._redis_client)
                    await asyncio.sleep(self._poll_interval)
                    continue

                # Dequeue next job (priority order: HIGH → MEDIUM → LOW)
                record = await self._queue.dequeue(priority_order())
                if record is None:
                    await asyncio.sleep(self._poll_interval)
                    continue

                # Dispatch
                await self._execute(record)

            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                log_swallowed_error(
                    "processor.worker_loop",
                    exc,
                    context={"worker_id": worker_id},
                )
                await asyncio.sleep(self._poll_interval)

        logger.debug("Worker loop %d exiting", worker_id)

    async def _execute(self, record: JobRecord) -> None:
        """Mark running, execute, and record outcome for a single job."""
        job_id = record.id
        try:
            await self._queue.mark_running(job_id)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(
                "processor.execute.mark_running",
                exc,
                context={"job_id": job_id},
            )
            # Can't reliably proceed without running state; abandon this tick.
            return

        job_class = self._job_registry.get(record.job_type)
        if job_class is None:
            logger.error(
                "processor.execute: unknown job_type=%r for job_id=%s — failing",
                record.job_type,
                job_id,
            )
            try:
                await self._queue.mark_failed(
                    job_id, f"unknown job_type: {record.job_type!r}"
                )
            except Exception as exc:  # noqa: BLE001
                log_swallowed_error("processor.execute.mark_failed_unknown_type", exc)
            return

        try:
            job: BaseJob = job_class(**record.payload)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(
                "processor.execute.instantiate",
                exc,
                context={"job_id": job_id, "job_type": record.job_type},
            )
            try:
                await self._queue.mark_failed(job_id, f"instantiation error: {exc}")
            except Exception as mf_exc:  # noqa: BLE001
                log_swallowed_error("processor.execute.mark_failed_instantiate", mf_exc)
            return

        progress_cb = self._make_progress_cb(record)

        chosen_model: str | None = None
        default_local: str | None = None
        if record.requires_llm:
            default_local = record.model or "ollama/local"
            estimated_tokens = record.estimated_tokens_in + record.estimated_tokens_out
            decision = None
            try:
                decision = await resolve_job_model(
                    self._redis_client,
                    default_local=default_local,
                    api_model=settings.CATEGORIZE_MODELS.get("smart", default_local),
                    estimated_tokens=estimated_tokens,
                )
            except Exception as exc:  # noqa: BLE001
                log_swallowed_error(
                    "processor.execute.resolve_job_model",
                    exc,
                    context={"job_id": job_id},
                )

            if decision is not None and decision.hold:
                logger.warning(
                    "processor.job_held job_id=%s reason=%s", job_id, decision.reason,
                )
                try:
                    # CL-5/AF-017: record the HELD terminal state, NOT COMPLETED —
                    # a cost-cap hold must be distinguishable from a real success.
                    await self._queue.mark_held(
                        job_id,
                        JobResult(
                            job_id=job_id,
                            actual_tokens_in=0,
                            actual_tokens_out=0,
                            metadata={"held": True, "reason": decision.reason},
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    log_swallowed_error("processor.execute.mark_held", exc)
                return

            chosen_model = (decision.model if decision is not None else None) or default_local

        job_start_monotonic = time.monotonic()
        try:
            if (
                record.requires_llm
                and chosen_model is not None
                and chosen_model != default_local
            ):
                with llm_call_override("openrouter", chosen_model):
                    raw_result: JobResult = await job.run(progress_cb=progress_cb)
            else:
                raw_result = await job.run(progress_cb=progress_cb)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(
                "processor.execute.run",
                exc,
                context={"job_id": job_id, "job_type": record.job_type},
            )
            await self._handle_failure(record, str(exc))
            return
        job_duration_s = time.monotonic() - job_start_monotonic

        # Fill in the job_id (contract: jobs leave it "" per Phase 3a spec)
        result = JobResult(
            job_id=job_id,
            actual_tokens_in=raw_result.actual_tokens_in,
            actual_tokens_out=raw_result.actual_tokens_out,
            metadata=raw_result.metadata,
        )

        try:
            await self._queue.mark_completed(job_id, result)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("processor.execute.mark_completed", exc)

        # Record metrics. LLM jobs price against the model actually used
        # (chosen_model) so hybrid-routed API jobs accrue real spend;
        # non-LLM jobs keep their pre-execution CostEstimate (their
        # cpu/* models aren't in the pricing table).
        if self._redis_client is not None:
            try:
                from app.processor.metrics import record_completion

                if record.requires_llm and chosen_model is not None:
                    try:
                        actual_cost = cost_estimator.estimate(
                            model=chosen_model,
                            tokens_in=(
                                result.actual_tokens_in
                                if result.actual_tokens_in is not None
                                else record.estimated_tokens_in
                            ),
                            tokens_out=(
                                result.actual_tokens_out
                                if result.actual_tokens_out is not None
                                else record.estimated_tokens_out
                            ),
                        ).estimated_usd
                    except Exception as exc:  # noqa: BLE001
                        log_swallowed_error(
                            "processor.execute.cost_estimate",
                            exc,
                            context={"job_id": job_id, "model": chosen_model},
                        )
                        if chosen_model != default_local:
                            logger.warning(
                                "processor.execute: cannot price chosen model=%r for "
                                "job_id=%s — the monthly spend cap cannot track this "
                                "job's cost; falling back to the pre-execution estimate",
                                chosen_model,
                                job_id,
                            )
                        actual_cost = job.estimate_cost().estimated_usd
                else:
                    actual_cost = job.estimate_cost().estimated_usd
                await record_completion(
                    self._redis_client,
                    job_id,
                    completed_at=datetime.now(tz=timezone.utc).timestamp(),
                    actual_cost_usd=actual_cost,
                    job_type=record.job_type,
                    duration_s=job_duration_s,
                )
            except Exception as exc:  # noqa: BLE001
                log_swallowed_error("processor.execute.record_completion", exc)

    async def _handle_failure(self, record: JobRecord, error_message: str) -> None:
        """Record failure and re-enqueue if retry budget remains."""
        job_id = record.id

        try:
            await self._queue.mark_failed(job_id, error_message)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error("processor.handle_failure.mark_failed", exc)

        new_retry_count = record.retry_count + 1
        if new_retry_count < self._max_retries:
            try:
                # Build a new record with bumped retry count.
                retry_record = JobRecord(
                    id=record.id,  # same logical job id
                    job_type=record.job_type,
                    state=JobState.PENDING,
                    priority=record.priority,
                    payload=record.payload,
                    enqueued_at=datetime.now(tz=timezone.utc),
                    retry_count=new_retry_count,
                    estimated_tokens_in=record.estimated_tokens_in,
                    estimated_tokens_out=record.estimated_tokens_out,
                    requires_llm=record.requires_llm,
                    model=record.model,
                )
                await self._queue.enqueue(retry_record)
                logger.info(
                    "processor.retry job_id=%s attempt=%d/%d",
                    job_id,
                    new_retry_count,
                    self._max_retries,
                )
            except Exception as exc:  # noqa: BLE001
                log_swallowed_error("processor.handle_failure.re_enqueue", exc)
        else:
            logger.warning(
                "processor.job_exhausted job_id=%s after %d attempts — no more retries",
                job_id,
                new_retry_count,
            )

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _is_throttled(self) -> bool:
        """Return True when system load exceeds the configured ceiling."""
        try:
            load = os.getloadavg()[0]
            return load > self._load_ceiling
        except (AttributeError, OSError):
            # Windows: os.getloadavg doesn't exist
            return False

    def _make_progress_cb(self, record: JobRecord):  # type: ignore[return]
        """Return an async progress callback that logs AND persists progress.

        CL-5: persisting the checkpoint onto the job record (via a lightweight
        single-field HSET) is what lets ``GET /processor/recent`` and dashboards
        surface a job's progress — previously it was debug-logged and lost.
        """
        job_id = record.id

        async def _cb(progress: float) -> None:
            logger.debug(
                "processor.progress job_id=%s pct=%.0f%%",
                job_id,
                progress * 100,
            )
            await self._queue.update_progress(job_id, progress)

        return _cb


# ---------------------------------------------------------------------------
# Registry factory
# ---------------------------------------------------------------------------


def build_default_registry() -> dict[str, type[BaseJob]]:
    """Return a registry of all concrete BaseJob subclasses in app.processor.jobs.

    Auto-discovers every module under ``app.processor.jobs`` (not just those
    re-exported by its ``__init__``) and walks ``BaseJob.__subclasses__()``
    recursively, so a new job file self-registers by existing — no manual
    ``__init__`` edit. A job whose class is never imported never becomes a
    BaseJob subclass, so it silently drops from the registry and every record
    enqueued for it fails at runtime with "unknown job_type"; importing the
    whole package closes that gap at its source.
    """
    import importlib
    import pkgutil

    import app.processor.jobs as _jobs_pkg

    for mod in pkgutil.iter_modules(_jobs_pkg.__path__):
        if mod.name.startswith("_"):
            continue
        importlib.import_module(f"{_jobs_pkg.__name__}.{mod.name}")

    registry: dict[str, type[BaseJob]] = {}
    _walk_subclasses(BaseJob, registry)  # type: ignore[arg-type]
    logger.info("processor.registry built: %s", list(registry.keys()))
    return registry


def _walk_subclasses(cls: type, registry: dict[str, type[BaseJob]]) -> None:
    """Recursively collect non-abstract BaseJob subclasses into registry."""
    for sub in cls.__subclasses__():
        # Skip abstract intermediaries (they have __abstractmethods__ set)
        jt: str = getattr(sub, "job_type", "")
        if not getattr(sub, "__abstractmethods__", None) and jt:
            registry[jt] = sub
        _walk_subclasses(sub, registry)
