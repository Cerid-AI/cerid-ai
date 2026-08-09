# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Verification telemetry (Phase 0.4a) — timeout/uncertain-rate visibility.

``core/agents/hallucination/verification.py`` cites a 26% uncertain rate
with no regression guard, and there was no timeout-rate telemetry at all.
This module records best-effort daily counters, called from
``app.db.neo4j.artifacts.save_verification_report`` right after a report
is persisted, and exposes an aggregated reader for
``GET /observability/verification-rates``.

Key pattern
-----------
``cerid:metrics:verification:{YYYYMMDD}:<counter>`` — a plain Redis
``INCRBY`` counter per UTC day, TTL'd so old days age out automatically.
Counters:

* ``claims_total``   — claims persisted in a report (the report's own
  ``total`` field, matching what was written to Neo4j).
* ``uncertain_count`` — claims with ``ClaimStatus.uncertain``.
* ``timeout_count``   — claims whose ``verification_method == "timeout"``
  (the exact string the verifier sets in
  ``core.agents.hallucination.verification`` on a per-claim deadline).
* ``reports_total``  — one increment per ``save_verification_report`` call.

Best-effort throughout: a Redis outage must never break verification
report persistence, so every writer/reader swallows exceptions via
``log_swallowed_error``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.observability.verification_metrics")

_KEY_PREFIX = "cerid:metrics:verification"
_TTL_S = 35 * 24 * 60 * 60  # ~35 days

_COUNTER_NAMES = ("claims_total", "uncertain_count", "timeout_count", "reports_total")
_7D_LOOKBACK_DAYS = 7


def _day_key(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).strftime("%Y%m%d")


def _counter_key(day: str, counter: str) -> str:
    return f"{_KEY_PREFIX}:{day}:{counter}"


def record_verification_report(
    *,
    claims_total: int,
    uncertain_count: int,
    timeout_count: int,
) -> None:
    """Best-effort daily-counter increment for one saved verification report.

    Called from ``save_verification_report`` immediately after the report
    is persisted. Never raises — a Redis outage must not break the save.
    """
    try:
        from app.deps import get_redis
        redis_client = get_redis()
        day = _day_key()
        pipe = redis_client.pipeline()
        pipe.incrby(_counter_key(day, "claims_total"), max(claims_total, 0))
        pipe.incrby(_counter_key(day, "uncertain_count"), max(uncertain_count, 0))
        pipe.incrby(_counter_key(day, "timeout_count"), max(timeout_count, 0))
        pipe.incr(_counter_key(day, "reports_total"))
        for counter in _COUNTER_NAMES:
            pipe.expire(_counter_key(day, counter), _TTL_S)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001 — telemetry must never break persistence
        log_swallowed_error("app.observability.verification_metrics.record", exc)


def _read_day_counters(redis_client: Any, day: str) -> dict[str, int]:
    counters: dict[str, int] = dict.fromkeys(_COUNTER_NAMES, 0)
    for name in _COUNTER_NAMES:
        raw = redis_client.get(_counter_key(day, name))
        if raw is not None:
            counters[name] = int(raw)
    return counters


def _with_rates(counters: dict[str, int]) -> dict[str, Any]:
    claims_total = counters["claims_total"]
    return {
        **counters,
        "timeout_rate": (
            round(counters["timeout_count"] / claims_total, 4) if claims_total else None
        ),
        "uncertain_rate": (
            round(counters["uncertain_count"] / claims_total, 4) if claims_total else None
        ),
    }


def get_verification_rates() -> dict[str, Any]:
    """Return today's and the trailing-7-day verification counters + rates.

    Shape::

        {
          "today": {claims_total, uncertain_count, timeout_count,
                     reports_total, timeout_rate, uncertain_rate},
          "last_7d": {... same keys, summed across the trailing 7 UTC days},
        }

    ``timeout_rate``/``uncertain_rate`` are ``None`` when ``claims_total``
    is zero (avoids a misleading 0.0 when there's simply no data yet).
    Best-effort — returns zeroed/None-rate shapes on any Redis failure so
    the endpoint never 503s.
    """
    empty = dict.fromkeys(_COUNTER_NAMES, 0)
    try:
        from app.deps import get_redis
        redis_client = get_redis()

        now = datetime.now(timezone.utc)
        today_counters = _read_day_counters(redis_client, _day_key(now))

        agg = dict.fromkeys(_COUNTER_NAMES, 0)
        for i in range(_7D_LOOKBACK_DAYS):
            day_counters = _read_day_counters(redis_client, _day_key(now - timedelta(days=i)))
            for name in _COUNTER_NAMES:
                agg[name] += day_counters[name]

        return {"today": _with_rates(today_counters), "last_7d": _with_rates(agg)}
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("app.observability.verification_metrics.get_rates", exc)
        return {"today": _with_rates(empty), "last_7d": _with_rates(empty)}
