# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""APScheduler cron registrations for brief generation.

Phase N.1 of v0.92 plan. These functions register cron triggers on an
existing :class:`~apscheduler.schedulers.asyncio.AsyncIOScheduler` instance.
The registered callables construct a concrete ``BaseJob`` subclass, build
a ``JobRecord`` via ``job.new_record()``, and enqueue it on the Background
Processor queue.

Wired into app/main.py lifespan.startup after Phase P's processor_queue
is assigned to ``app.state``. The expected import + call sequence:

    from app.scheduler import get_scheduler
    from app.services.briefs.scheduler import (
        schedule_daily_brief, schedule_weekly_synthesis,
    )

    scheduler = get_scheduler()
    if scheduler is not None:
        schedule_daily_brief(scheduler, app.state.processor_queue)
        schedule_weekly_synthesis(scheduler, app.state.processor_queue)

Note: this file lives under ``app/services/briefs/`` (not ``app/scheduler/``)
because the directory ``app/scheduler/`` would shadow the existing
``app/scheduler.py`` maintenance-engine module at import time. The brief
scheduler is logically part of the brief service surface.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger("ai-companion.briefs.scheduler")


# ---------------------------------------------------------------------------
# Cron job callables
# ---------------------------------------------------------------------------


async def _daily_brief_job(queue: object) -> None:
    """Enqueue a BriefGenerationJob for today's date."""
    from datetime import date

    from app.processor.jobs.brief_generation import BriefGenerationJob

    target_date = date.today().isoformat()
    job = BriefGenerationJob(target_date=target_date)
    record = job.new_record(payload={"target_date": target_date})
    await queue.enqueue(record)  # type: ignore[attr-defined]
    logger.info("brief_generation enqueued target_date=%s job_id=%s", target_date, record.id)


async def _weekly_synthesis_job(queue: object) -> None:
    """Enqueue a WeeklySynthesisJob for the current week's ending date (Monday)."""
    from datetime import date, timedelta

    from app.processor.jobs.weekly_synthesis import WeeklySynthesisJob

    today = date.today()
    # Find the most recent Monday (weekday 0)
    days_since_monday = today.weekday()  # 0 = Monday … 6 = Sunday
    monday = today - timedelta(days=days_since_monday)
    week_ending = monday.isoformat()

    job = WeeklySynthesisJob(week_ending=week_ending)
    record = job.new_record(payload={"week_ending": week_ending})
    await queue.enqueue(record)  # type: ignore[attr-defined]
    logger.info(
        "weekly_synthesis enqueued week_ending=%s job_id=%s", week_ending, record.id
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def schedule_daily_brief(
    scheduler: "AsyncIOScheduler",
    processor_queue: object,
) -> None:
    """Register a daily brief cron at 06:00 local time.

    The cron callback constructs a ``BriefGenerationJob`` for today,
    builds a ``JobRecord``, and enqueues it on the Background Processor
    queue so the processor's throttling, retry, and cost-tracking
    machinery applies.

    Parameters
    ----------
    scheduler
        Running :class:`~apscheduler.schedulers.asyncio.AsyncIOScheduler`
        instance.
    processor_queue
        Any object satisfying ``JobQueueProtocol`` (i.e. has an
        ``async enqueue(record: JobRecord)`` method). Typically
        ``app.state.processor_queue``.
    """
    scheduler.add_job(
        _daily_brief_job,
        trigger="cron",
        hour=6,
        minute=0,
        args=[processor_queue],
        id="daily_brief",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info("Registered daily_brief cron at 06:00 local")


def schedule_weekly_synthesis(
    scheduler: "AsyncIOScheduler",
    processor_queue: object,
) -> None:
    """Register a weekly synthesis cron on Monday at 06:00 local time.

    Parameters
    ----------
    scheduler
        Running :class:`~apscheduler.schedulers.asyncio.AsyncIOScheduler`
        instance.
    processor_queue
        Any object satisfying ``JobQueueProtocol``. Typically
        ``app.state.processor_queue``.
    """
    scheduler.add_job(
        _weekly_synthesis_job,
        trigger="cron",
        day_of_week="mon",
        hour=6,
        minute=0,
        args=[processor_queue],
        id="weekly_synthesis",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info("Registered weekly_synthesis cron at Monday 06:00 local")
