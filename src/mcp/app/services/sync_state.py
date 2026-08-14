# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Shared per-connector sync/ingest state — the one source of truth every
status surface reads (sf-1 status truth).

Before this module, each surface derived "is a sync happening?" from a
different partial signal: /ingestion/progress tracked only in-process file
jobs (so connector ingests showed ``files: []`` at 100% CPU), the web
Sources hero computed a rate from event logs, and the desktop connector
card showed whatever its last IPC round-trip returned — running was
indistinguishable from stalled.

Model
-----
One Redis hash per connector, ``cerid:sync:state:<connector>``:

Client-reported (the syncing client — e.g. the desktop main process —
POSTs absolute counters through ``/ingestion/sync-state/{connector}``):

* ``phase``        — "syncing" | "idle" | "error" (client's own claim)
* ``total``        — items this sync intends to process (0 = unknown)
* ``scanned``      — items scanned so far
* ``posted``       — items POSTed to the ingest API so far
* ``failed``       — items that failed client-side
* ``last_error``   — most recent error string

Server-observed (written by the ingest endpoints themselves, so they are
true regardless of what any client claims):

* ``ingested_total`` — lifetime artifacts actually ingested for this
  connector (reconcilable with KB artifact counts)
* ``deduped_total``  — lifetime duplicate-collapsed posts
* ``errored_total``  — lifetime posts that errored server-side
* ``last_ingest_at`` — timestamp of the most recent server-side ingest

Window bookkeeping (set by a ``phase="syncing"`` report):

* ``window_started_at``      — when the current sync began
* ``window_ingested_start``  — ``ingested_total`` snapshot at that moment

Derived on read (never stored): ``state`` ("syncing" | "stalled" |
"ingesting" | "error" | "idle"), ``window_ingested``, ``rate_per_min``,
``eta_seconds``. "stalled" is a client that said "syncing" but has not
touched the hash within ``SYNC_ACTIVE_WINDOW_S`` — the exact running-vs-
stalled ambiguity UX-24 diagnosed, made visible instead of guessed at.

All writes are best-effort against the provided Redis client; readers get
[] / None on Redis failure rather than an exception.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.sync_state")

_KEY_PREFIX = "cerid:sync:state:"

# A connector hash untouched for this long ages out entirely.
STATE_TTL_S = 14 * 24 * 3600

# A "syncing" client silent for longer than this is reported as stalled;
# a server-side ingest older than this no longer counts as live activity.
SYNC_ACTIVE_WINDOW_S = 120

_CLIENT_PHASES = ("syncing", "idle", "error")

_COUNTER_FIELDS = ("total", "scanned", "posted", "failed")


def _key(connector: str) -> str:
    return f"{_KEY_PREFIX}{connector}"


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _to_int(raw: Any) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _hgetall_str(redis: Any, key: str) -> dict[str, str]:
    raw = redis.hgetall(key) or {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        ks = k.decode() if isinstance(k, bytes) else str(k)
        vs = v.decode() if isinstance(v, bytes) else str(v)
        out[ks] = vs
    return out


def report_sync(
    redis: Any,
    connector: str,
    *,
    phase: str,
    total: int | None = None,
    scanned: int | None = None,
    posted: int | None = None,
    failed: int | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    """Merge a client progress report into the connector's state hash.

    Counters are absolutes (idempotent under at-least-once delivery), never
    increments. A ``phase="syncing"`` report on a connector not already
    syncing opens a new window: counters reset and ``window_ingested_start``
    snapshots the lifetime ingested count. Returns the derived state, or
    ``None`` when Redis is unavailable.
    """
    if phase not in _CLIENT_PHASES:
        raise ValueError(f"phase must be one of {_CLIENT_PHASES}, got {phase!r}")
    try:
        key = _key(connector)
        current = _hgetall_str(redis, key)
        mapping: dict[str, str] = {
            "connector": connector,
            "phase": phase,
            "updated_at": utcnow_iso(),
        }
        if phase == "syncing" and current.get("phase") != "syncing":
            mapping["window_started_at"] = utcnow_iso()
            mapping["window_ingested_start"] = current.get("ingested_total", "0")
            for field in _COUNTER_FIELDS:
                mapping[field] = "0"
        for field, value in (
            ("total", total), ("scanned", scanned), ("posted", posted), ("failed", failed),
        ):
            if value is not None:
                mapping[field] = str(max(0, int(value)))
        if error is not None:
            mapping["last_error"] = error[:500]
        redis.hset(key, mapping=mapping)
        redis.expire(key, STATE_TTL_S)
        return get_sync_state(redis, connector)
    except Exception as exc:  # noqa: BLE001 — status reporting must never break a sync
        log_swallowed_error("services.sync_state.report", exc, redis_client=redis)
        return None


def record_ingest_outcome(redis: Any, connector: str, status: str) -> None:
    """Count a server-side ingest outcome for ``connector``.

    Called by the ingest endpoints with the pipeline's own result status, so
    the ``ingested_total`` a surface renders is what the server actually did
    — not what a client believes it posted. Best-effort.
    """
    if not connector:
        return
    try:
        key = _key(connector)
        if status in ("success", "updated"):
            redis.hincrby(key, "ingested_total", 1)
        elif status == "duplicate":
            redis.hincrby(key, "deduped_total", 1)
        else:
            redis.hincrby(key, "errored_total", 1)
        redis.hset(key, mapping={
            "connector": connector,
            "last_ingest_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
        })
        redis.expire(key, STATE_TTL_S)
    except Exception as exc:  # noqa: BLE001 — counting must never break an ingest
        log_swallowed_error("services.sync_state.record", exc, redis_client=redis)


def _derive(raw: dict[str, str]) -> dict[str, Any]:
    """Compute the read-side view (state / rate / ETA) from a raw hash."""
    now = _now()
    phase = raw.get("phase", "idle")
    updated_at = _parse_iso(raw.get("updated_at"))
    last_ingest_at = _parse_iso(raw.get("last_ingest_at"))
    recently_updated = (
        updated_at is not None and (now - updated_at).total_seconds() <= SYNC_ACTIVE_WINDOW_S
    )
    recently_ingesting = (
        last_ingest_at is not None
        and (now - last_ingest_at).total_seconds() <= SYNC_ACTIVE_WINDOW_S
    )

    if phase == "error":
        state = "error"
    elif phase == "syncing":
        state = "syncing" if (recently_updated or recently_ingesting) else "stalled"
    elif recently_ingesting:
        # No client window open, but the server is actively ingesting for
        # this connector (e.g. a scheduled poll) — that is activity, not idle.
        state = "ingesting"
    else:
        state = "idle"

    ingested_total = _to_int(raw.get("ingested_total"))
    window_start = _parse_iso(raw.get("window_started_at"))
    window_ingested = max(0, ingested_total - _to_int(raw.get("window_ingested_start")))

    rate_per_min: float | None = None
    eta_seconds: int | None = None
    total = _to_int(raw.get("total"))
    if phase == "syncing" and window_start is not None:
        elapsed_s = max(1.0, (now - window_start).total_seconds())
        rate_per_min = round(window_ingested / (elapsed_s / 60.0), 2)
        done = max(window_ingested, _to_int(raw.get("posted")))
        if total > 0 and rate_per_min > 0:
            eta_seconds = int((total - min(done, total)) / (rate_per_min / 60.0))

    return {
        "connector": raw.get("connector", ""),
        "state": state,
        "phase": phase,
        "total": total,
        "scanned": _to_int(raw.get("scanned")),
        "posted": _to_int(raw.get("posted")),
        "failed": _to_int(raw.get("failed")),
        "ingested_total": ingested_total,
        "deduped_total": _to_int(raw.get("deduped_total")),
        "errored_total": _to_int(raw.get("errored_total")),
        "window_ingested": window_ingested,
        "rate_per_min": rate_per_min,
        "eta_seconds": eta_seconds,
        "window_started_at": raw.get("window_started_at") or None,
        "last_ingest_at": raw.get("last_ingest_at") or None,
        "updated_at": raw.get("updated_at") or None,
        "last_error": raw.get("last_error") or None,
    }


def get_sync_state(redis: Any, connector: str) -> dict[str, Any] | None:
    """The derived state for one connector, or None when absent/unreachable."""
    try:
        raw = _hgetall_str(redis, _key(connector))
    except Exception as exc:  # noqa: BLE001 — readers degrade to "unknown"
        log_swallowed_error("services.sync_state.get", exc, redis_client=redis)
        return None
    if not raw:
        return None
    return _derive(raw)


def get_all_sync_states(redis: Any) -> list[dict[str, Any]]:
    """Derived state for every connector that has ever reported, most
    recently updated first. [] on Redis failure — readers render "unknown",
    never crash."""
    try:
        states: list[dict[str, Any]] = []
        for key in redis.scan_iter(match=f"{_KEY_PREFIX}*", count=100):
            key_str = key.decode() if isinstance(key, bytes) else str(key)
            raw = _hgetall_str(redis, key_str)
            if raw:
                states.append(_derive(raw))
        states.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
        return states
    except Exception as exc:  # noqa: BLE001 — readers degrade to "unknown"
        log_swallowed_error("services.sync_state.get_all", exc, redis_client=redis)
        return []
