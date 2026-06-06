# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Inference-path degradation observability.

The inference path degrades gracefully: each workload prefers its configured
provider (Quenchforge GPU) and silently falls back to a local ONNX runtime
(embed / rerank) or OpenRouter (LLM) when the local backend is unreachable.
Those fallbacks *work*, but historically they were invisible — ``/health``
reported the *configured* provider while the system was actually serving from a
fallback, so an operator could not tell that the GPU path was dead.

This module records the TRUE serving state. Call sites report each outcome via
:func:`record_success` / :func:`record_fallback`; ``/health`` reads
:func:`snapshot` (merged into ``inference_routing`` via :func:`annotate_block`)
so it reflects reality instead of intent.

Contract: in-process, thread-safe, never raises (observability must not break
the inference call path it instruments). Single-worker uvicorn → module state is
shared across requests; a multi-worker deployment would scope state per worker,
which is acceptable for a degradation signal.
"""

from __future__ import annotations

import threading
import time
from typing import Any

# A degradation older than this (with no refresh and no success-clear) is no
# longer reported as current — avoids a stale "red" lingering after recovery
# when nothing has exercised the workload since.
_DEGRADED_TTL_S = 900.0

_LOCK = threading.Lock()
_EVENTS: dict[str, dict[str, Any]] = {}


def reset() -> None:
    """Clear all recorded state (tests + a fresh process)."""
    with _LOCK:
        _EVENTS.clear()


def record_fallback(
    workload: str,
    *,
    configured: str,
    served_by: str,
    detail: str = "",
) -> None:
    """Record that ``configured`` was unavailable and ``served_by`` took over.

    ``workload`` is one of ``"llm"`` / ``"embed"`` / ``"rerank"`` (free-form;
    matched against the ``inference_routing`` keys). Never raises.
    """
    try:
        if not workload:
            return
        with _LOCK:
            ev = _EVENTS.setdefault(workload, {})
            ev["configured"] = configured or ev.get("configured", "unknown")
            ev["served_by"] = served_by or "fallback"
            ev["degraded"] = True
            ev["detail"] = (detail or "")[:200]
            ev["last_event_ts"] = time.time()
            ev["fallback_count"] = int(ev.get("fallback_count", 0)) + 1
    except Exception:  # noqa: BLE001 — observability must never break the caller
        pass


def record_success(workload: str, *, provider: str) -> None:
    """Record that ``provider`` (the configured backend) served successfully.

    Clears any standing degradation for the workload. Never raises.
    """
    try:
        if not workload:
            return
        with _LOCK:
            ev = _EVENTS.setdefault(workload, {})
            ev["configured"] = provider or ev.get("configured", "unknown")
            ev["served_by"] = provider or ev.get("served_by", "unknown")
            ev["degraded"] = False
            ev["last_event_ts"] = time.time()
    except Exception:  # noqa: BLE001 — observability must never break the caller
        pass


def _status_for(ev: dict[str, Any], now: float) -> dict[str, Any]:
    age = now - float(ev.get("last_event_ts", 0.0))
    degraded = bool(ev.get("degraded")) and age <= _DEGRADED_TTL_S
    return {
        "configured": ev.get("configured", "unknown"),
        "serving": ev.get("served_by", ev.get("configured", "unknown")),
        "degraded": degraded,
        "detail": ev.get("detail", "") if degraded else "",
        "fallback_count": int(ev.get("fallback_count", 0)),
        "age_s": round(age, 1),
    }


def snapshot() -> dict[str, dict[str, Any]]:
    """Per-workload serving/degraded view of the inference path. Never raises."""
    out: dict[str, dict[str, Any]] = {}
    try:
        with _LOCK:
            now = time.time()
            for workload, ev in _EVENTS.items():
                out[workload] = _status_for(ev, now)
    except Exception:  # noqa: BLE001 — observability fallback
        pass
    return out


def annotate_block(workload: str, block: dict[str, Any]) -> dict[str, Any]:
    """Merge the live serving/degraded signal into an ``inference_routing`` block.

    ``block`` carries the *configured* intent (``provider``/``model``); this adds
    ``serving`` (what actually answered last) + ``degraded``. A workload with no
    recorded event is reported as ``degraded: False`` serving its configured
    provider (the optimistic default — nothing has failed). Never raises.
    """
    try:
        snap = snapshot().get(workload)
        if snap is None:
            block.setdefault("serving", block.get("provider", "unknown"))
            block.setdefault("degraded", False)
            return block
        block["serving"] = snap["serving"]
        block["degraded"] = snap["degraded"]
        if snap["degraded"] and snap["detail"]:
            block["degraded_detail"] = snap["detail"]
        block["fallback_count"] = snap["fallback_count"]
    except Exception:  # noqa: BLE001 — observability fallback
        block.setdefault("degraded", False)
    return block
