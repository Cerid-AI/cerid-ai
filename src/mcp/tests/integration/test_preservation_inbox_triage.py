# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase J inbox triage preservation invariants."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.preservation


def test_feature_flag_declared():
    """inbox_triage feature flag must remain in FEATURE_FLAGS — the
    plugin register() path + scheduler gate + MCP tools all depend
    on it."""
    from config.features import FEATURE_FLAGS
    assert "inbox_triage" in FEATURE_FLAGS


def test_agent_public_surface_stable():
    """utils + agent + dataclasses are the contract the scheduler +
    MCP tools depend on. Drift here breaks both call sites."""
    from core.agents.inbox_triage import (
        CATEGORIES,
        TriagedThread,
        triage_inboxes,
    )
    assert callable(triage_inboxes)
    assert "urgent" in CATEGORIES
    assert "actionable" in CATEGORIES
    assert "newsletter" in CATEGORIES
    assert "promo" in CATEGORIES
    assert "personal" in CATEGORIES
    # Dataclasses have the canonical field set
    sample = TriagedThread(
        thread_id="t", source="gmail", participants=[],
        subject="x", message_count=1, latest_at="0",
        category="actionable", summary="s", suggested_action="a",
    )
    d = sample.__dict__ if hasattr(sample, "__dict__") else {}
    for field in (
        "thread_id", "source", "category", "summary",
        "suggested_action", "participants", "subject",
    ):
        assert field in d


def test_mcp_tools_registered():
    """Both inbox tools must be in the registry — used by the chat
    surface and the connector panel's manual trigger button."""
    from app.mcp_tools import inbox  # noqa: F401
    from app.tool_registry import TOOL_REGISTRY
    assert "pkb_inbox_triage" in TOOL_REGISTRY
    assert "pkb_inbox_filter" in TOOL_REGISTRY


def test_schedule_setting_exposed():
    """SCHEDULE_INBOX_TRIAGE must remain on the settings module so
    the scheduler can read it. Default cadence stays at every 15 min."""
    from config import settings
    assert hasattr(settings, "SCHEDULE_INBOX_TRIAGE")
    # Default cron is every 15 min
    assert settings.SCHEDULE_INBOX_TRIAGE in ("*/15 * * * *", "")


def test_scheduler_job_function_callable():
    """The scheduler imports _run_inbox_triage by name — it must
    stay a top-level async function on app.scheduler."""
    import inspect

    from app.scheduler import _run_inbox_triage
    assert inspect.iscoroutinefunction(_run_inbox_triage)
