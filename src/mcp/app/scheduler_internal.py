# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Internal-only trading scheduler jobs.

This file exists only in cerid-ai-internal. The bootstrap block at
the bottom of scheduler.py calls register_trading_jobs() to add
trading-specific cron jobs to the APScheduler instance.
"""
from __future__ import annotations

import logging
import time

import config
from app.deps import get_neo4j
from app.scheduler import _log_execution

logger = logging.getLogger("ai-companion.scheduler")


async def _run_trading_autoresearch() -> None:
    """Pull performance summary from trading agent and store in KB."""
    start = time.time()
    try:
        from agents.trading_scheduler_jobs import run_trading_autoresearch
        result = await run_trading_autoresearch(
            trading_agent_url=config.TRADING_AGENT_URL,
            neo4j=get_neo4j(),
        )
        status = result.get("status", "unknown")
        duration = time.time() - start
        _log_execution("trading_autoresearch", status, duration)
        logger.info(f"Scheduled trading autoresearch: {status} in {duration:.1f}s")
    except Exception as e:
        duration = time.time() - start
        _log_execution("trading_autoresearch", "error", duration, str(e))
        logger.error(f"Scheduled trading autoresearch failed: {e}")


async def _run_platt_scaling_mirror() -> None:
    """Mirror Platt calibration params from trading agent to Neo4j."""
    start = time.time()
    try:
        from agents.trading_scheduler_jobs import run_platt_scaling_mirror
        result = await run_platt_scaling_mirror(
            trading_agent_url=config.TRADING_AGENT_URL,
            neo4j=get_neo4j(),
        )
        mirrored = result.get("mirrored", 0)
        duration = time.time() - start
        _log_execution("platt_scaling_mirror", result.get("status", "unknown"), duration, f"{mirrored} mirrored")
        logger.info(f"Scheduled Platt mirror: {mirrored} mirrored in {duration:.1f}s")
    except Exception as e:
        duration = time.time() - start
        _log_execution("platt_scaling_mirror", "error", duration, str(e))
        logger.error(f"Scheduled Platt mirror failed: {e}")


async def _run_longshot_surface_rebuild() -> None:
    """Rebuild calibration surface from trading agent data."""
    start = time.time()
    try:
        from agents.trading_scheduler_jobs import run_longshot_surface_rebuild
        result = await run_longshot_surface_rebuild(
            trading_agent_url=config.TRADING_AGENT_URL,
            neo4j=get_neo4j(),
        )
        status = result.get("status", "unknown")
        points = result.get("points_stored", 0)
        duration = time.time() - start
        _log_execution("longshot_surface_rebuild", status, duration, f"{points} points")
        logger.info(f"Scheduled longshot surface rebuild: {status} ({points} points) in {duration:.1f}s")
    except Exception as e:
        duration = time.time() - start
        _log_execution("longshot_surface_rebuild", "error", duration, str(e))
        logger.error(f"Scheduled longshot surface rebuild failed: {e}")


def register_trading_jobs(scheduler) -> None:
    """Register trading cron jobs if CERID_TRADING_ENABLED and schedules are configured."""
    from apscheduler.triggers.cron import CronTrigger

    if not getattr(config, "CERID_TRADING_ENABLED", False):
        return

    _trading_schedule = getattr(config, "SCHEDULE_TRADING_AUTORESEARCH", "")
    if _trading_schedule:
        scheduler.add_job(
            _run_trading_autoresearch,
            CronTrigger.from_crontab(_trading_schedule),
            id="trading_autoresearch",
            name="Trading auto-research",
            replace_existing=True,
        )
    _platt_schedule = getattr(config, "SCHEDULE_PLATT_MIRROR", "")
    if _platt_schedule:
        scheduler.add_job(
            _run_platt_scaling_mirror,
            CronTrigger.from_crontab(_platt_schedule),
            id="platt_scaling_mirror",
            name="Platt scaling mirror",
            replace_existing=True,
        )
    _longshot_schedule = getattr(config, "SCHEDULE_LONGSHOT_SURFACE", "")
    if _longshot_schedule:
        scheduler.add_job(
            _run_longshot_surface_rebuild,
            CronTrigger.from_crontab(_longshot_schedule),
            id="longshot_surface_rebuild",
            name="Longshot surface rebuild",
            replace_existing=True,
        )
    logger.info("Trading scheduler jobs registered (CERID_TRADING_ENABLED=true)")
