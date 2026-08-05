# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Observable counterpart to ``except Exception: pass``.

The FE has an equivalent helper (``src/web/src/lib/log-swallowed.ts``)
that the v0.84.0 silent-catch sweep adopted across 43 call sites. The
Python side had no equivalent — swallowed errors in the parsers /
ingestion / LLM-fallback paths logged locally but never incremented a
counter, so "ingestion silently degrading" was invisible until a user
complained.

This module provides:

* ``log_swallowed_error(module, exc, *, context, redis_client)`` — drop-in
  replacement for ``logger.warning(...)`` inside a broad catch.
* ``swallowed_error_counts(redis_client, *, window_s=3600)`` — dashboard
  accessor returning ``{module: count}`` for the last window.

The Redis counter is a sorted set keyed by module, scored by timestamp;
``zremrangebyscore`` trims entries older than the window before each
read. The accounting side-effects are themselves guarded — observability
must never itself raise.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("ai-companion.swallowed")

_REDIS_KEY_FMT = "cerid:swallowed:{module}"
_DEFAULT_WINDOW_S = 3600


# ---------------------------------------------------------------------------
# Redis DI sink (core/ must never import app/ — see docs/CONVENTIONS.md).
#
# Only ~25 of 1,000+ log_swallowed_error call sites passed redis_client=, so the
# `cerid:swallowed:*` counters were never written and
# /health.swallowed_errors_last_hour reported a permanent zero while jobs failed
# nightly. Rather than touch 1,000 call sites, the app layer installs a getter
# once at startup and the helper resolves Redis itself.
# ---------------------------------------------------------------------------
_redis_sink: Callable[[], Any] | None = None


def set_swallowed_redis_sink(fn: Callable[[], Any] | None) -> None:
    """Install (or clear) the callable that returns a Redis client."""
    global _redis_sink
    _redis_sink = fn


def _resolve_redis() -> Any | None:
    """Best-effort Redis handle from the sink; never raises."""
    if _redis_sink is None:
        return None
    try:
        return _redis_sink()
    except Exception:  # noqa: BLE001  # silent-catch-meta: observability must never raise
        return None


def log_swallowed_error(
    module: str,
    exc: BaseException,
    *,
    context: dict[str, Any] | None = None,
    redis_client: Any | None = None,
) -> None:
    """Record a swallowed exception.

    Always logs at WARNING with module + exc type. When a Redis client is
    provided, increments ``cerid:swallowed:<module>`` so
    ``swallowed_error_counts()`` can surface the rate. Sentry breadcrumbs
    are added when the SDK is installed — the swallow is intentional, so
    we do NOT ``capture_exception``.

    Parameters
    ----------
    module
        The logical subsystem swallowing the error. Stable string — do
        not use ``__name__`` because it changes across package splits.
        Examples: ``"ingestion.ai_categorize"``, ``"parsers.html_strip"``.
    exc
        The exception instance. Only type + str(exc) are logged; the
        traceback is NOT captured (the swallow is intentional).
    context
        Optional structured metadata for log correlation.
    redis_client
        Optional Redis client; absent = counter skipped.
    """
    logger.warning(
        "swallowed %s in %s: %s",
        type(exc).__name__,
        module,
        exc,
        extra={"swallowed_module": module, **(context or {})},
    )
    try:
        import sentry_sdk  # type: ignore[import-not-found]
        sentry_sdk.add_breadcrumb(
            category="swallowed",
            message=f"{type(exc).__name__} in {module}: {exc}",
            level="warning",
            data=context or {},
        )
    except ImportError:
        pass
    except Exception:  # noqa: BLE001  # silent-catch-meta: observability layer must never raise/recurse
        pass

    if redis_client is None:
        redis_client = _resolve_redis()
    if redis_client is None:
        return
    try:
        ts_ms = int(time.time() * 1000)
        key = _REDIS_KEY_FMT.format(module=module)
        redis_client.zadd(key, {f"{ts_ms}:{id(exc)}": ts_ms})
        # Trim the sliding window on every write. O(log N) amortized.
        cutoff = ts_ms - _DEFAULT_WINDOW_S * 1000
        redis_client.zremrangebyscore(key, 0, cutoff)
    except Exception:  # noqa: BLE001  # silent-catch-meta: observability layer must never raise/recurse
        pass


def swallowed_error_counts(
    redis_client: Any,
    *,
    window_s: int = _DEFAULT_WINDOW_S,
    known_modules: tuple[str, ...] = (
        "ingestion.ai_categorize",
        "parsers.html_strip",
    ),
) -> dict[str, int]:
    """Return ``{module: count}`` for the past ``window_s`` seconds.

    Safe to call even when the Redis key doesn't exist. Unknown keys
    simply return 0. ``known_modules`` seeds the dict so dashboards
    show 0-counts explicitly (better than absent keys).
    """
    out: dict[str, int] = dict.fromkeys(known_modules, 0)
    if redis_client is None:
        return out
    now_ms = int(time.time() * 1000)
    cutoff = now_ms - window_s * 1000
    try:
        # Scan for any module keys that exist even if not in known_modules.
        for key in redis_client.scan_iter(match="cerid:swallowed:*"):
            name = (
                key.decode() if isinstance(key, bytes) else str(key)
            ).replace("cerid:swallowed:", "", 1)
            try:
                redis_client.zremrangebyscore(key, 0, cutoff)
                out[name] = int(redis_client.zcard(key))
            except Exception:  # noqa: BLE001
                out.setdefault(name, 0)
    except Exception:  # noqa: BLE001  # silent-catch-meta: observability layer must never raise/recurse
        pass
    return out
