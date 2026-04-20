# src/mcp/app/observability/sentry_init.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Centralised Sentry init. No-op when SENTRY_DSN is unset — privacy-first default."""
from __future__ import annotations

import os
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.httpx import HttpxIntegration
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

# Third-party loggers whose errors are cosmetic and should never reach Sentry.
# chromadb 0.5.x ships a posthog-based ClientStartEvent telemetry call that
# crashes on newer posthog releases even when anonymized_telemetry=False is set
# (chromadb instantiates the telemetry module at import time). Ignoring the
# logger drops the event from Sentry without affecting stdout logging.
_IGNORED_LOGGERS = (
    "chromadb.telemetry.product.posthog",
)

# Provider API keys — not covered by DEFAULT_DENYLIST (which covers generic "api_key").
# Session / cookie data — defensive for the planned multi-user mode.
_EXTRA_DENYLIST = [
    "openrouter_api_key", "anthropic_api_key", "xai_api_key", "openai_api_key",
    "X-API-Key",
    # Session / cookie data — defensive for the planned multi-user mode.
    "cookies", "set-cookie", "x-session-id",
]

_NOISY_POLL_SUBSTRINGS = ("/ingestion/progress", "/health", "/observability/queue-depth")
# Note: "/health" will also match /observability/health-score (polled by the
# observability dashboard) — that's incidentally desirable; it's noisy too.


def _traces_sampler(sampling_context: dict[str, Any]) -> float:
    """Per-transaction sample-rate decision.

    Reads the transaction name from ``sampling_context['transaction_context']['name']``
    (the Sentry SDK 2.35+ shape; earlier SDKs also passed a ``'span_context'`` on
    child spans). Poll endpoints are down-sampled to 0.01 — enough to surface
    latency regressions without drowning the dashboard. Everything else takes
    the ``SENTRY_TRACES_SAMPLE_RATE`` default (0.1).
    """
    ctx = (
        sampling_context.get("transaction_context")
        or sampling_context.get("span_context")
        or {}
    )
    txn = ctx.get("name") or ""
    for noisy in _NOISY_POLL_SUBSTRINGS:
        if noisy in txn:
            return 0.01
    return float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))


def init_sentry() -> bool:
    """Initialise Sentry with tracing + profiling enabled.

    Returns True iff Sentry was actually initialised. No-op when
    SENTRY_DSN is empty — keeps local dev dependency-free and privacy-preserving.
    """
    dsn = os.getenv("SENTRY_DSN_MCP") or os.getenv("SENTRY_DSN")
    if not dsn:
        return False

    profiles_rate = float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.1"))

    for logger_name in _IGNORED_LOGGERS:
        ignore_logger(logger_name)

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("CERID_ENVIRONMENT") or os.getenv("SENTRY_ENVIRONMENT", "development"),
        release=os.getenv("CERID_VERSION") or os.getenv("SENTRY_RELEASE"),
        traces_sampler=_traces_sampler,
        profiles_sample_rate=profiles_rate,
        send_default_pii=False,
        event_scrubber=EventScrubber(
            denylist=DEFAULT_DENYLIST + _EXTRA_DENYLIST,
            recursive=True,
        ),
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            StarletteIntegration(transaction_style="endpoint"),
            HttpxIntegration(),
            RedisIntegration(),
            LoggingIntegration(level=None, event_level=None),
        ],
        max_breadcrumbs=50,
        enable_logs=True,
    )
    return True
