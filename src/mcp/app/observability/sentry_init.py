# src/mcp/app/observability/sentry_init.py
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Centralised Sentry init. No-op when SENTRY_DSN is unset — privacy-first default."""
from __future__ import annotations

import os
import time
from collections import defaultdict
from threading import Lock
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.httpx import HttpxIntegration
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber
from sentry_sdk.types import Event, Hint

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


# Per-fingerprint rate limit. Sentry already groups duplicate events into
# one issue via server-side fingerprinting, but every individual event
# still costs quota — and a single dependency outage in a hot loop can
# burn thousands of events in minutes. The 2026-04-26 Neo4j WAL incident
# emitted 1,716 events in ~1h for one issue group before we caught it.
# We cap each fingerprint at MAX events per WINDOW seconds at the SDK
# level so even a sustained crashloop can't melt the quota.
_RATE_LIMIT_WINDOW_S = 60
_RATE_LIMIT_MAX = 5
_RATE_LIMIT_TTL_S = 600  # drop fingerprints idle longer than this (memory cap)
_rate_limit_state: dict[str, list[float]] = defaultdict(list)
_rate_limit_lock = Lock()


def _event_fingerprint(event: Event) -> str:
    """Stable key per error class — mirrors Sentry's grouping, simplified.

    Custom ``event["fingerprint"]`` wins when set. Otherwise we key by
    (exception type + transaction name) which collapses the same error
    raised from the same endpoint regardless of stack-trace addresses.
    """
    custom = event.get("fingerprint")
    if custom:
        return ":".join(str(c) for c in custom)
    exc_values = (event.get("exception") or {}).get("values") or []
    exc_type = exc_values[0].get("type", "") if exc_values else ""
    return f"{exc_type}|{event.get('transaction', '')}"


def _rate_limited(event: Event) -> bool:
    """Return True when the event's fingerprint already fired
    ``_RATE_LIMIT_MAX`` times within the last ``_RATE_LIMIT_WINDOW_S``
    seconds. Side-effect: prunes expired timestamps and drops idle
    fingerprints to keep memory bounded.
    """
    fp = _event_fingerprint(event)
    now = time.time()
    with _rate_limit_lock:
        ts_list = _rate_limit_state[fp]
        ts_list[:] = [t for t in ts_list if now - t < _RATE_LIMIT_WINDOW_S]
        if len(ts_list) >= _RATE_LIMIT_MAX:
            return True
        ts_list.append(now)
        # Opportunistic GC: any fingerprint whose newest entry is older
        # than the TTL is unreachable from rate-limit decisions and can
        # be dropped. Cheap because the active set is small.
        for stale in [k for k, v in _rate_limit_state.items() if v and now - v[-1] > _RATE_LIMIT_TTL_S]:
            del _rate_limit_state[stale]
        return False


def _before_send(event: Event, hint: Hint) -> Event | None:
    """Drop events that exceed the per-fingerprint rate limit. Returning
    ``None`` from a Sentry ``before_send`` hook tells the SDK to discard
    the event entirely (no transport, no quota cost).
    """
    if _rate_limited(event):
        return None
    return event


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
        before_send=_before_send,
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
