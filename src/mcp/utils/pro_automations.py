# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Automation runtime settings store (UX consolidation pass).

Phase J + K shipped with env-only gates (CERID_INBOX_TRIAGE_ENABLED,
CERID_DAILY_DIGEST_ENABLED). That worked for operator-installed
deployments but had zero discoverability for Pro users.

This module backs a Redis hash that the UI can read/write at runtime,
overriding the env defaults. Schedulers + agents check this store
first; fall back to env when the Redis key is absent (so existing
deployments keep their behavior).

Layout:
    cerid:automations:<feature>:enabled = "true"|"false"
    cerid:automations:<feature>:schedule = "<cron expr>"

Features registered today: inbox_triage, daily_digest.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.automations")

# Feature registry — keep this lockstep with config/features.py
AUTOMATIONS: dict[str, dict[str, Any]] = {
    "inbox_triage": {
        "feature_flag": "inbox_triage",
        "env_enabled": "CERID_INBOX_TRIAGE_ENABLED",
        "env_schedule": "SCHEDULE_INBOX_TRIAGE",
        "default_schedule": "*/15 * * * *",
        "display_name": "Inbox Triage",
        "description": "AI categorize Gmail + Outlook threads "
                       "(urgent / actionable / personal / newsletter / promo)",
        "cadence_presets": [
            {"label": "Off", "cron": ""},
            {"label": "Every 15 minutes", "cron": "*/15 * * * *"},
            {"label": "Every hour", "cron": "0 * * * *"},
            {"label": "Every 4 hours", "cron": "0 */4 * * *"},
        ],
    },
    "daily_digest": {
        "feature_flag": "daily_digest",
        "env_enabled": "CERID_DAILY_DIGEST_ENABLED",
        "env_schedule": "SCHEDULE_DAILY_DIGEST",
        "default_schedule": "0 7 * * *",
        "display_name": "Daily Digest",
        "description": "Scheduled LLM summary of the last 24 hours "
                       "(top categories, urgent, action items, quality alerts)",
        "cadence_presets": [
            {"label": "Off", "cron": ""},
            {"label": "Morning (7 AM UTC)", "cron": "0 7 * * *"},
            {"label": "Evening (6 PM UTC)", "cron": "0 18 * * *"},
            {"label": "Weekdays at 8 AM UTC", "cron": "0 8 * * 1-5"},
        ],
    },
}


def _key(feature: str, field: str) -> str:
    return f"cerid:automations:{feature}:{field}"


def _env_bool(env_var: str) -> bool:
    return os.getenv(env_var, "false").lower() in ("1", "true", "yes")


def is_enabled(feature: str) -> bool:
    """Effective enabled state. Redis override wins; env fallback when
    Redis is unavailable or the key is absent.

    Returns False (safe default) on any unknown error so the scheduler
    doesn't kick off LLM work uncontrollably.
    """
    spec = AUTOMATIONS.get(feature)
    if not spec:
        return False
    try:
        from app.deps import get_redis
        redis = get_redis()
        if redis is not None:
            raw = redis.get(_key(feature, "enabled"))
            if raw is not None:
                if isinstance(raw, bytes):
                    raw = raw.decode()
                return str(raw).lower() in ("1", "true", "yes")
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "utils.pro_automations.is_enabled_redis",
            exc,
            context={"feature": feature},
        )
    # Fall through to env default
    return _env_bool(spec["env_enabled"])


def get_schedule(feature: str) -> str:
    """Effective cron expression. Redis override wins. Empty string ==
    disabled (scheduler skips registration entirely)."""
    spec = AUTOMATIONS.get(feature)
    if not spec:
        return ""
    try:
        from app.deps import get_redis
        redis = get_redis()
        if redis is not None:
            raw = redis.get(_key(feature, "schedule"))
            if raw is not None:
                return raw.decode() if isinstance(raw, bytes) else str(raw)
    except Exception as exc:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "utils.pro_automations.get_schedule_redis",
            exc,
            context={"feature": feature},
        )
    return os.getenv(spec["env_schedule"], spec["default_schedule"])


def set_enabled(feature: str, enabled: bool) -> None:
    if feature not in AUTOMATIONS:
        raise KeyError(f"unknown automation: {feature}")
    from app.deps import get_redis
    redis = get_redis()
    if redis is None:
        raise RuntimeError("redis unavailable — cannot persist automation state")
    redis.set(_key(feature, "enabled"), "true" if enabled else "false")


def set_schedule(feature: str, cron: str) -> None:
    if feature not in AUTOMATIONS:
        raise KeyError(f"unknown automation: {feature}")
    # Tolerate empty string ("disable" sentinel); reject syntactically
    # broken non-empty crons.
    if cron:
        _validate_cron(cron)
    from app.deps import get_redis
    redis = get_redis()
    if redis is None:
        raise RuntimeError("redis unavailable — cannot persist schedule")
    redis.set(_key(feature, "schedule"), cron)


def reset(feature: str) -> None:
    """Clear Redis overrides for a feature — falls back to env defaults."""
    if feature not in AUTOMATIONS:
        raise KeyError(f"unknown automation: {feature}")
    from app.deps import get_redis
    redis = get_redis()
    if redis is None:
        return
    redis.delete(_key(feature, "enabled"))
    redis.delete(_key(feature, "schedule"))


def get_state(feature: str) -> dict[str, Any]:
    """Snapshot of effective config + UI metadata for the automations
    panel. Combines runtime state with the static feature spec."""
    spec = AUTOMATIONS.get(feature)
    if not spec:
        raise KeyError(f"unknown automation: {feature}")
    try:
        from config.features import is_feature_enabled
        flag_on = bool(is_feature_enabled(spec["feature_flag"]))
    except ImportError:
        flag_on = False
    return {
        "feature": feature,
        "display_name": spec["display_name"],
        "description": spec["description"],
        "feature_flag": spec["feature_flag"],
        "feature_flag_enabled": flag_on,
        "enabled": is_enabled(feature),
        "schedule": get_schedule(feature),
        "default_schedule": spec["default_schedule"],
        "cadence_presets": spec["cadence_presets"],
    }


def list_states() -> list[dict[str, Any]]:
    return [get_state(name) for name in AUTOMATIONS]


# ── cron validation ──────────────────────────────────────────────────

def _validate_cron(expr: str) -> None:
    """Cheap structural validator — five whitespace-separated fields
    of allowed characters. Doesn't catch every misuse but rejects
    obvious garbage so we don't propagate bad expressions into
    APScheduler (which would log noise on every restart).
    """
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron expression must have 5 fields, got {len(parts)}: {expr!r}")
    allowed = set("0123456789*/,-?")
    for i, part in enumerate(parts):
        if not part:
            raise ValueError(f"empty field at position {i} in {expr!r}")
        if not set(part).issubset(allowed):
            raise ValueError(f"illegal character in field {i}: {part!r}")
