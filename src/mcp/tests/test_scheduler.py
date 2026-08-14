# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for scheduled maintenance engine."""

import sys
from unittest.mock import MagicMock

import pytest

# Dependency stubs (chromadb, neo4j, redis, apscheduler, etc.) are handled
# by conftest.py pytest_configure(). We still need a deps stub since
# scheduler.py imports from deps at module level.
sys.modules.setdefault("deps", MagicMock())

from app.scheduler import get_job_status, start_scheduler, stop_scheduler  # noqa: E402


class TestSchedulerJobStatus:
    def test_not_running(self):
        """When scheduler isn't started, status should be not_running."""
        stop_scheduler()
        status = get_job_status()
        assert status["status"] == "not_running"
        assert status["jobs"] == []

    @pytest.mark.asyncio
    async def test_stop(self):
        """After stopping, scheduler should report not_running."""
        start_scheduler()
        stop_scheduler()
        status = get_job_status()
        assert status["status"] == "not_running"


class TestTriggerJob:
    """Contract for the manual job-trigger endpoint backing function.

    trigger_job resolves the callable from the live scheduler's own job
    record, so triggerability is defined by 'is it scheduled right now'.
    """

    def test_unknown_job_raises_keyerror(self, monkeypatch):
        """A job not live in the scheduler → KeyError (router maps to 404)."""
        from app import scheduler as sched

        fake_scheduler = MagicMock()
        fake_scheduler.get_job.return_value = None
        monkeypatch.setattr(sched, "_scheduler", fake_scheduler)
        monkeypatch.setattr(sched, "_manual_running", set())

        with pytest.raises(KeyError):
            sched.trigger_job("definitely_not_a_job")

    def test_scheduler_not_running_raises_valueerror(self, monkeypatch):
        """No live scheduler → ValueError (router maps to 409)."""
        from app import scheduler as sched

        monkeypatch.setattr(sched, "_scheduler", None)
        with pytest.raises(ValueError, match="not running"):
            sched.trigger_job("compute_umap_3d")

    def test_already_running_raises_valueerror(self, monkeypatch):
        """A second trigger while one is in flight → ValueError (coalesced)."""
        from app import scheduler as sched

        fake_scheduler = MagicMock()
        fake_scheduler.get_job.return_value = MagicMock(name="job")
        monkeypatch.setattr(sched, "_scheduler", fake_scheduler)
        monkeypatch.setattr(sched, "_manual_running", {"compute_umap_3d"})

        with pytest.raises(ValueError, match="already running"):
            sched.trigger_job("compute_umap_3d")

    def test_returns_started_payload_with_cache_patterns(self, monkeypatch):
        """A live job fires and reports the caches it will invalidate."""
        from app import scheduler as sched

        async def _fake_job():
            return None

        job = MagicMock()
        job.name = "Constellation 3D coordinate compute"
        job.func = _fake_job
        job.args = ()
        job.kwargs = {}
        fake_scheduler = MagicMock()
        fake_scheduler.get_job.return_value = job
        monkeypatch.setattr(sched, "_scheduler", fake_scheduler)
        monkeypatch.setattr(sched, "_manual_running", set())
        # Don't actually schedule the coroutine on a loop in this sync test.
        monkeypatch.setattr(sched.asyncio, "create_task", lambda coro: coro.close())

        result = sched.trigger_job("compute_umap_3d")
        assert result["status"] == "started"
        assert result["id"] == "compute_umap_3d"
        assert result["invalidates"] == ["cerid:graph:emb3d:*"]


class TestSourcePollGatesProKinds:
    """`_run_source_poll` walked `_POLLABLE_KINDS` with no feature check, and
    that tuple includes `apple_mail`. The job is registered unconditionally at
    */15, so a community install with such a source on record kept ingesting
    mail indefinitely — unlike `_run_daily_digest` and `_run_inbox_triage`,
    which both gate before doing any work.

    (`apple_reminders` was in the tuple too until its container-side connector
    was removed 2026-08-12 — it now ingests through the desktop app and has no
    pollable backend at all.)

    POST /sources now refuses to CREATE them, which does nothing for sources
    created before that landed — and those are exactly the rows this loop
    walks.
    """

    def test_the_pollable_set_still_contains_the_pro_kinds_this_guards(self):
        """If someone removes them the gate is moot, but so is the risk —
        this pins that the two facts stay in sync."""
        from app.scheduler import _POLLABLE_KINDS
        from core.ingest.sources.kinds import KIND_TIER

        pro = [k for k in _POLLABLE_KINDS if KIND_TIER.get(k) == "pro"]
        assert set(pro) == {"apple_mail"}

    def test_desktop_app_kinds_are_not_pollable(self):
        """A kind whose ingestion lives in the desktop app has no backend
        connector; polling it would be a poll of nothing. apple_reminders
        regressing INTO this tuple would resurrect the dead container-side
        surface RA-03 removed."""
        from app.routers.sources import _DESKTOP_APP_KINDS
        from app.scheduler import _POLLABLE_KINDS

        overlap = set(_POLLABLE_KINDS) & _DESKTOP_APP_KINDS
        assert not overlap, f"desktop-app kinds must not be polled: {sorted(overlap)}"

    def test_core_kinds_are_never_gated(self):
        """rss and url_watch are core; gating the whole JOB instead of each
        KIND would have silently stopped community ingestion."""
        from app.scheduler import _POLLABLE_KINDS
        from core.ingest.sources.kinds import KIND_TIER

        assert KIND_TIER.get("rss") == "core"
        assert KIND_TIER.get("url_watch") == "core"
        assert {"rss", "url_watch"} <= set(_POLLABLE_KINDS)

    def test_pro_kinds_are_skipped_at_community_tier(self, monkeypatch):
        """The behavioural assertion: at community tier the loop must not ask
        the registry for a Pro connector at all."""
        import asyncio
        from unittest.mock import MagicMock, patch

        import app.scheduler as sched

        asked: list[str] = []

        def _fake_get_connector(kind):
            asked.append(kind)
            return None

        # Patch on app.scheduler: get_redis/get_neo4j are imported at MODULE
        # scope there, so patching app.deps.* leaves the bound names untouched
        # and the job bails on a real Redis connection before ever reaching the
        # loop under test.
        monkeypatch.setattr("config.features.is_tier_met", lambda _t: False)
        with patch("core.ingest.sources.registry.get_connector", _fake_get_connector), \
             patch.object(sched, "get_redis", MagicMock()), \
             patch.object(sched, "get_neo4j", MagicMock()), \
             patch("app.db.neo4j.sources.list_sources", return_value=[]):
            asyncio.run(sched._run_source_poll())

        # Assert the loop RAN before asserting what it skipped. Without this
        # the two negative assertions pass trivially whenever the function
        # bails early — which is exactly what happened the first time this was
        # written: removing the gate left the test green.
        assert "rss" in asked, (
            "the poll loop never reached the registry, so the negative "
            f"assertions below would prove nothing (asked={asked})"
        )
        assert "apple_mail" not in asked, "Pro kind polled at community tier"
        assert "apple_reminders" not in asked, "Pro kind polled at community tier"


class TestTriggerJobDuplicateHonesty:
    """SF-2 — run-now must never silently no-op against a pending duplicate.

    A restart-orphaned (stale) marker must be superseded so the trigger runs
    for real; a genuinely live duplicate must be reported as collapsed, not
    'started', and no success execution record may be written without a run.
    """

    @staticmethod
    def _fake_redis():
        import fakeredis

        server = fakeredis.FakeServer()
        return fakeredis.FakeRedis(server=server, decode_responses=True)

    @staticmethod
    def _seed_pending(redis_client, *, stale: bool):
        import uuid
        from datetime import datetime, timedelta, timezone

        import config
        from app.db.redis.processor_queue import _record_to_mapping
        from core.processor.job import JobRecord, JobState
        from core.processor.priority import Priority

        enqueued_at = datetime.now(tz=timezone.utc)
        if stale:
            enqueued_at -= timedelta(seconds=config.PROCESSOR_PENDING_STALE_TTL_S + 60)
        record = JobRecord(
            id=str(uuid.uuid4()),
            job_type="compute_umap_3d",
            state=JobState.PENDING,
            priority=Priority.LOW,
            payload={},
            enqueued_at=enqueued_at,
        )
        redis_client.hset(f"cerid:proc:job:{record.id}", mapping=_record_to_mapping(record))
        redis_client.lpush("cerid:proc:queue:low", record.id)
        return record

    def _trigger_with_marker(self, monkeypatch, *, stale: bool):
        from app import scheduler as sched

        redis_client = self._fake_redis()
        self._seed_pending(redis_client, stale=stale)

        job = MagicMock()
        job.name = "Constellation 3D coordinate compute"

        async def _fake_job():
            return None

        job.func = _fake_job
        job.args = ()
        job.kwargs = {}
        fake_scheduler = MagicMock()
        fake_scheduler.get_job.return_value = job
        monkeypatch.setattr(sched, "_scheduler", fake_scheduler)
        monkeypatch.setattr(sched, "_manual_running", set())
        monkeypatch.setattr(sched, "get_redis", lambda: redis_client)
        monkeypatch.setattr(sched.asyncio, "create_task", lambda coro: coro.close())
        return sched.trigger_job("compute_umap_3d"), redis_client

    def test_live_pending_duplicate_reports_collapsed_not_started(self, monkeypatch):
        result, _redis = self._trigger_with_marker(monkeypatch, stale=False)
        assert result["status"] == "collapsed_into_pending"
        assert result["id"] == "compute_umap_3d"
        assert "existing_job_id" in result
        assert "no new run was started" in result["detail"]

    def test_stale_orphaned_marker_does_not_absorb_run_now(self, monkeypatch):
        result, redis_client = self._trigger_with_marker(monkeypatch, stale=True)
        assert result["status"] == "started"
        # The orphaned marker was pruned, not left to absorb the next tick too.
        assert redis_client.llen("cerid:proc:queue:low") == 0

    @pytest.mark.asyncio
    async def test_collapsed_manual_run_logs_no_success(self, monkeypatch):
        """Backstop: even when the collapse happens inside the job body (race
        past the pre-check), the manual wrapper must not log success."""
        from app import scheduler as sched

        logged: list[tuple[str, str, str]] = []
        monkeypatch.setattr(
            sched, "_log_execution",
            lambda name, status, duration, detail="": logged.append((name, status, detail)),
        )
        monkeypatch.setattr(sched, "_manual_running", set())

        class _CollapsingQueue:
            async def enqueue_if_absent(self, record):
                return None

        class _StubJob:
            def new_record(self, *, payload=None):
                return MagicMock()

        async def _body():
            await sched._enqueue_periodic(_CollapsingQueue(), _StubJob(), "compute_umap_3d", 0.0)

        await sched._run_and_invalidate("compute_umap_3d", _body, (), {})
        statuses = [status for (_n, status, _d) in logged]
        assert "success" not in statuses
        assert "skipped" in statuses

    @pytest.mark.asyncio
    async def test_real_manual_run_still_logs_success(self, monkeypatch):
        from app import scheduler as sched

        logged: list[tuple[str, str]] = []
        monkeypatch.setattr(
            sched, "_log_execution",
            lambda name, status, duration, detail="": logged.append((name, status)),
        )
        monkeypatch.setattr(sched, "_manual_running", set())
        monkeypatch.setattr(sched, "_bust_job_caches", lambda _job_id: 0)

        async def _body():
            return None

        await sched._run_and_invalidate("rectify", _body, (), {})
        assert ("rectify", "success") in logged
