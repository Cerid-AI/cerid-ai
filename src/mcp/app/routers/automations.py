# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""User-facing scheduled automations — recurring knowledge tasks (digests, research, auto-ingest)."""
from __future__ import annotations

import json
import logging
import time
import uuid
from enum import Enum
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.deps import get_redis
from core.utils.time import utcnow_iso

_logger = logging.getLogger("ai-companion.automations")

router = APIRouter(prefix="/automations", tags=["automations"])

# ---------------------------------------------------------------------------
# Redis key helpers
# ---------------------------------------------------------------------------
_KEY_PREFIX = "cerid:automations"
_RUN_PREFIX = "cerid:automation_runs"
_DIGEST_PREFIX = "cerid:automation_digest"


def _auto_key(automation_id: str) -> str:
    return f"{_KEY_PREFIX}:{automation_id}"


def _run_key(automation_id: str, run_id: str) -> str:
    return f"{_RUN_PREFIX}:{automation_id}:{run_id}"


def _run_index_key(automation_id: str) -> str:
    """Sorted set holding run_ids ordered by timestamp for history queries."""
    return f"{_RUN_PREFIX}:{automation_id}:index"


# ---------------------------------------------------------------------------
# Schedule presets
# ---------------------------------------------------------------------------
SCHEDULE_PRESETS = {
    "daily_morning": {"label": "Daily at 9am", "cron": "0 9 * * *"},
    "daily_evening": {"label": "Daily at 6pm", "cron": "0 18 * * *"},
    "weekdays_morning": {"label": "Weekdays at 9am", "cron": "0 9 * * 1-5"},
    "weekly_monday": {"label": "Weekly on Monday", "cron": "0 9 * * 1"},
    "monthly_first": {"label": "Monthly on 1st", "cron": "0 9 1 * *"},
}

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class AutomationAction(str, Enum):
    NOTIFY = "notify"
    DIGEST = "digest"
    INGEST = "ingest"


class AutomationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    prompt: str = Field(..., min_length=1, max_length=5000)
    schedule: str = Field(..., min_length=5, max_length=100)
    action: AutomationAction = AutomationAction.NOTIFY
    domains: list[str] = Field(default_factory=lambda: ["general"])
    enabled: bool = True


class AutomationUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    prompt: str | None = None
    schedule: str | None = None
    action: AutomationAction | None = None
    domains: list[str] | None = None
    enabled: bool | None = None


class Automation(AutomationCreate):
    id: str
    created_at: str
    updated_at: str
    last_run_at: str | None = None
    last_status: str | None = None
    run_count: int = 0


class AutomationRun(BaseModel):
    automation_id: str
    run_id: str
    started_at: str
    completed_at: str | None = None
    status: str
    result: dict | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _save_automation(auto: Automation) -> None:
    r = get_redis()
    r.set(_auto_key(auto.id), json.dumps(auto.model_dump()))


def _load_automation(automation_id: str) -> Automation | None:
    r = get_redis()
    raw = r.get(_auto_key(automation_id))
    if raw is None:
        return None
    return Automation(**json.loads(raw))


def _delete_automation_data(automation_id: str) -> None:
    r = get_redis()
    r.delete(_auto_key(automation_id))
    # Clean up run history
    index_key = _run_index_key(automation_id)
    run_ids = r.zrange(index_key, 0, -1)
    if run_ids:
        pipe = r.pipeline()
        for rid in run_ids:
            rid_str = rid if isinstance(rid, str) else rid.decode()
            pipe.delete(_run_key(automation_id, rid_str))
        pipe.delete(index_key)
        pipe.execute()
    # Clean up digest accumulator
    r.delete(f"{_DIGEST_PREFIX}:{automation_id}")


def _list_automations() -> list[Automation]:
    r = get_redis()
    keys = r.keys(f"{_KEY_PREFIX}:*")
    automations: list[Automation] = []
    if not keys:
        return automations
    values = r.mget(keys)
    for raw in values:
        if raw is not None:
            try:
                automations.append(Automation(**json.loads(raw)))
            except (json.JSONDecodeError, ValueError, KeyError):
                pass
    automations.sort(key=lambda a: a.created_at, reverse=True)
    return automations


def _save_run(run: AutomationRun) -> None:
    r = get_redis()
    r.set(_run_key(run.automation_id, run.run_id), json.dumps(run.model_dump()))
    r.zadd(
        _run_index_key(run.automation_id),
        {run.run_id: time.time()},
    )
    # Trim to last 50 runs
    r.zremrangebyrank(_run_index_key(run.automation_id), 0, -51)


def _get_run_history(automation_id: str, limit: int = 20) -> list[AutomationRun]:
    r = get_redis()
    run_ids = r.zrevrange(_run_index_key(automation_id), 0, limit - 1)
    if not run_ids:
        return []
    runs: list[AutomationRun] = []
    for rid in run_ids:
        rid_str = rid if isinstance(rid, str) else rid.decode()
        raw = r.get(_run_key(automation_id, rid_str))
        if raw is not None:
            try:
                runs.append(AutomationRun(**json.loads(raw)))
            except Exception:
                pass
    return runs


# ---------------------------------------------------------------------------
# Execution engine
# ---------------------------------------------------------------------------

_DIGEST_THRESHOLD = 5  # accumulate this many results before summarizing


async def execute_automation(automation: Automation) -> AutomationRun:
    """Execute an automation's prompt through the full agent pipeline."""
    run_id = str(uuid.uuid4())
    now = utcnow_iso()
    run = AutomationRun(
        automation_id=automation.id,
        run_id=run_id,
        started_at=now,
        status="running",
    )
    _save_run(run)

    start = time.time()
    try:
        # Run query through agent pipeline
        from core.agents.query_agent import agent_query

        result = await agent_query(
            query=automation.prompt,
            domains=automation.domains if automation.domains else None,
            top_k=10,
            use_reranking=True,
        )

        # Handle action type
        if automation.action == AutomationAction.INGEST:
            await _handle_ingest(automation, result)
        elif automation.action == AutomationAction.DIGEST:
            await _handle_digest(automation, result)
        # NOTIFY: result is stored in run history, pushed via SSE by caller

        duration = time.time() - start
        run.status = "success"
        run.completed_at = utcnow_iso()
        run.result = {
            "confidence": result.get("confidence", 0),
            "source_count": len(result.get("sources", [])),
            "context_length": len(result.get("context", "")),
            "duration_s": round(duration, 2),
        }

    except (RuntimeError, ValueError, TypeError, KeyError, OSError) as e:
        duration = time.time() - start
        _logger.error("Automation %s (%s) failed: %s", automation.id, automation.name, e)
        run.status = "error"
        run.completed_at = utcnow_iso()
        run.error = str(e)

    _save_run(run)

    # Update automation metadata
    automation.last_run_at = run.completed_at or now
    automation.last_status = run.status
    automation.run_count += 1
    _save_automation(automation)

    return run


async def _handle_ingest(automation: Automation, result: dict[str, Any]) -> None:
    """Ingest the query result into KB."""
    context = result.get("context", "")
    if not context:
        return
    from app.services.ingestion import ingest_content

    domain = automation.domains[0] if automation.domains else "general"
    ingest_content(
        content=context,
        domain=domain,
        metadata={
            "filename": f"automation-{automation.id}.md",
            "source": "automation",
            "automation_id": automation.id,
            "automation_name": automation.name,
        },
    )
    _logger.info("Automation %s ingested result into domain=%s", automation.id, domain)


async def _handle_digest(automation: Automation, result: dict[str, Any]) -> None:
    """Accumulate results in Redis list. When threshold is reached, summarize and store."""
    context = result.get("context", "")
    if not context:
        return
    r = get_redis()
    digest_key = f"{_DIGEST_PREFIX}:{automation.id}"
    r.rpush(digest_key, json.dumps({
        "context": context[:2000],
        "confidence": result.get("confidence", 0),
        "timestamp": utcnow_iso(),
    }))
    count = r.llen(digest_key)
    if count >= _DIGEST_THRESHOLD:
        # Pull all accumulated items and clear
        items_raw = r.lrange(digest_key, 0, -1)
        r.delete(digest_key)
        items = [json.loads(i) for i in items_raw]
        summary_parts = [f"[{it['timestamp']}] (conf={it['confidence']}) {it['context'][:500]}" for it in items]
        summary = f"# Digest: {automation.name}\n\n" + "\n\n---\n\n".join(summary_parts)

        from app.services.ingestion import ingest_content

        domain = automation.domains[0] if automation.domains else "general"
        ingest_content(
            content=summary,
            domain=domain,
            metadata={
                "filename": f"digest-{automation.id}-{utcnow_iso()[:10]}.md",
                "source": "automation_digest",
                "automation_id": automation.id,
                "automation_name": automation.name,
            },
        )
        _logger.info("Automation %s digest flushed (%d items) into domain=%s", automation.id, len(items), domain)


# ---------------------------------------------------------------------------
# APScheduler integration
# ---------------------------------------------------------------------------


def _validate_cron(cron_expr: str) -> None:
    """Validate a cron expression by attempting to parse it."""
    from apscheduler.triggers.cron import CronTrigger

    try:
        CronTrigger.from_crontab(cron_expr)
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=422, detail=f"Invalid cron expression: {e}") from e


def _register_job(automation: Automation) -> None:
    """Register an automation as an APScheduler job."""
    from apscheduler.triggers.cron import CronTrigger

    from app.scheduler import get_scheduler

    sched = get_scheduler()
    if sched is None:
        _logger.warning("Scheduler not running — automation %s will activate on restart", automation.id)
        return

    async def _job_wrapper() -> None:
        auto = _load_automation(automation.id)
        if auto is None or not auto.enabled:
            return
        await execute_automation(auto)

    job_id = f"automation:{automation.id}"
    sched.add_job(
        _job_wrapper,
        CronTrigger.from_crontab(automation.schedule),
        id=job_id,
        name=f"Automation: {automation.name}",
        replace_existing=True,
    )
    _logger.info("Registered scheduler job %s (cron=%s)", job_id, automation.schedule)


def _unregister_job(automation_id: str) -> None:
    """Remove an automation's APScheduler job if it exists."""
    from app.scheduler import get_scheduler

    sched = get_scheduler()
    if sched is None:
        return
    job_id = f"automation:{automation_id}"
    try:
        sched.remove_job(job_id)
        _logger.info("Removed scheduler job %s", job_id)
    except (KeyError, ValueError):
        pass  # Job may not exist


def register_all_automations() -> int:
    """Load all enabled automations from Redis and register with APScheduler.

    Called during startup after the scheduler is running.
    """
    automations = _list_automations()
    registered = 0
    for auto in automations:
        if auto.enabled:
            try:
                _register_job(auto)
                registered += 1
            except Exception as e:
                _logger.warning("Failed to register automation %s: %s", auto.id, e)
    if registered:
        _logger.info("Registered %d user automations with scheduler", registered)
    return registered


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_automations():
    """List all automations."""
    return [a.model_dump() for a in _list_automations()]


@router.post("", status_code=201)
async def create_automation(req: AutomationCreate):
    """Create a new automation."""
    _validate_cron(req.schedule)
    now = utcnow_iso()
    auto = Automation(
        id=str(uuid.uuid4()),
        created_at=now,
        updated_at=now,
        **req.model_dump(),
    )
    _save_automation(auto)
    if auto.enabled:
        _register_job(auto)
    _logger.info("Created automation %s: %s", auto.id, auto.name)
    return auto.model_dump()


@router.get("/presets")
async def get_presets():
    """Return cron schedule presets."""
    return SCHEDULE_PRESETS


@router.get("/{automation_id}")
async def get_automation(automation_id: str):
    """Get a single automation by ID."""
    auto = _load_automation(automation_id)
    if auto is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    return auto.model_dump()


@router.put("/{automation_id}")
async def update_automation(automation_id: str, req: AutomationUpdate):
    """Update an automation."""
    auto = _load_automation(automation_id)
    if auto is None:
        raise HTTPException(status_code=404, detail="Automation not found")

    update_data = req.model_dump(exclude_unset=True)
    if "schedule" in update_data:
        _validate_cron(update_data["schedule"])

    for key, value in update_data.items():
        setattr(auto, key, value)
    auto.updated_at = utcnow_iso()
    _save_automation(auto)

    # Re-register or unregister based on enabled state
    if auto.enabled:
        _register_job(auto)
    else:
        _unregister_job(auto.id)

    _logger.info("Updated automation %s: %s", auto.id, auto.name)
    return auto.model_dump()


@router.delete("/{automation_id}")
async def delete_automation(automation_id: str):
    """Delete an automation and its history."""
    auto = _load_automation(automation_id)
    if auto is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    _unregister_job(automation_id)
    _delete_automation_data(automation_id)
    _logger.info("Deleted automation %s: %s", automation_id, auto.name)
    return {"status": "deleted", "id": automation_id}


@router.post("/{automation_id}/enable")
async def enable_automation(automation_id: str):
    """Enable an automation."""
    auto = _load_automation(automation_id)
    if auto is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    auto.enabled = True
    auto.updated_at = utcnow_iso()
    _save_automation(auto)
    _register_job(auto)
    return auto.model_dump()


@router.post("/{automation_id}/disable")
async def disable_automation(automation_id: str):
    """Disable an automation."""
    auto = _load_automation(automation_id)
    if auto is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    auto.enabled = False
    auto.updated_at = utcnow_iso()
    _save_automation(auto)
    _unregister_job(auto.id)
    return auto.model_dump()


@router.post("/{automation_id}/run")
async def trigger_manual_run(automation_id: str):
    """Trigger an immediate manual run of an automation."""
    auto = _load_automation(automation_id)
    if auto is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    run = await execute_automation(auto)
    return run.model_dump()


@router.get("/{automation_id}/history")
async def get_history(automation_id: str, limit: int = 20):
    """Get execution history for an automation (last N runs)."""
    auto = _load_automation(automation_id)
    if auto is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    limit = min(max(limit, 1), 50)
    runs = _get_run_history(automation_id, limit=limit)
    return [r.model_dump() for r in runs]
