# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Daily digest preservation invariants — Phase K Day 3."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.preservation


def test_daily_digest_feature_flag_declared():
    from config.features import FEATURE_FLAGS
    assert "daily_digest" in FEATURE_FLAGS


def test_digests_domain_in_taxonomy():
    """The 'digests' domain must be in TAXONOMY so list_artifacts +
    rag_weights.known_sources both see it."""
    from config.taxonomy import DOMAINS, TAXONOMY
    assert "digests" in TAXONOMY
    assert "digests" in DOMAINS


def test_schedule_setting_exposed():
    from config import settings
    assert hasattr(settings, "SCHEDULE_DAILY_DIGEST")
    # Default daily 7 AM
    assert settings.SCHEDULE_DAILY_DIGEST in ("0 7 * * *", "")


def test_agent_public_surface():
    from core.agents.daily_digest import (
        DigestResult,
        DigestSection,
        generate_daily_digest,
    )
    assert callable(generate_daily_digest)
    # Fields the scheduler + REST handlers consume
    sample = DigestResult()
    for field in (
        "digest_id", "generated_at", "window_hours", "top_categories",
        "key_threads", "urgent", "action_items", "quality_alerts",
        "artifact_count", "flagged_count", "inbox_urgent_count",
        "skipped", "skip_reason", "persisted_artifact_id",
    ):
        assert hasattr(sample, field)
    assert hasattr(DigestSection, "__dataclass_fields__")


def test_scheduler_job_is_async():
    """_run_daily_digest must remain a top-level async function so the
    APScheduler add_job(...) registration works."""
    import inspect

    from app.scheduler import _run_daily_digest
    assert inspect.iscoroutinefunction(_run_daily_digest)


def test_digests_endpoint_shape(http_client):
    """GET /digests/latest responds even when no digests exist."""
    r = http_client.get("/digests/latest")
    # 200 (returns None) or 403 (feature off) — never 500
    assert r.status_code in (200, 403), f"/digests/latest {r.status_code}: {r.text[:200]}"


def test_run_now_blocked_without_feature(http_client):
    """POST /digests/run-now must reject (403) when feature off.
    When on, the test environment may not have neo4j → we accept any
    non-500 response since the agent's neo4j-unavailable path is
    tested in the unit suite."""
    r = http_client.post("/digests/run-now")
    assert r.status_code in (200, 403, 422)
