# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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


def test_run_now_feature_gate(http_client):
    """POST /digests/run-now: 403 when the feature is off, 202 when on.

    Digest generation now runs as a background processor job
    (``DigestRunJob``) — the endpoint just enqueues (or returns the
    already-active job) and acks 202, so the feature-on branch is cheap
    to assert. The pre-async version of this test had to structured-skip
    feature-on instances because the inline digest ran for minutes
    (observed 2026-07-12 against the pro-tier master)."""
    probe = http_client.get("/digests/latest")
    r = http_client.post("/digests/run-now")
    if probe.status_code == 403:
        assert r.status_code == 403, f"/digests/run-now {r.status_code}: {r.text[:200]}"
    else:
        assert r.status_code == 202, f"/digests/run-now {r.status_code}: {r.text[:200]}"
        body = r.json()
        assert body.get("status") == "queued"
        assert body.get("job_id")
