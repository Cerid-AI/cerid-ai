# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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

from core.utils.swallowed import log_swallowed_error

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
    except Exception as exc:  # noqa: BLE001 — observability must never break the caller
        log_swallowed_error(__name__, exc)


def record_success(workload: str, *, provider: str, model: str = "") -> None:
    """Record that ``provider`` (the configured backend) served successfully.

    ``model`` is the model the backend reported on the response, when it reports
    one. Quenchforge forwards the requested name to whichever slot is loaded and
    answers with the slot's real name, so the request model is intent and this
    is the fact — ``/health`` surfaces it as ``serving_model``.

    Clears any standing degradation for the workload. Never raises.
    """
    try:
        if not workload:
            return
        with _LOCK:
            ev = _EVENTS.setdefault(workload, {})
            ev["configured"] = provider or ev.get("configured", "unknown")
            ev["served_by"] = provider or ev.get("served_by", "unknown")
            ev["served_model"] = model or ""
            ev["degraded"] = False
            ev["last_event_ts"] = time.time()
    except Exception as exc:  # noqa: BLE001 — observability must never break the caller
        log_swallowed_error(__name__, exc)


def _status_for(ev: dict[str, Any], now: float) -> dict[str, Any]:
    # A degradation stands until a SUCCESS clears it. It used to expire after
    # 15 minutes of quiet, which erased the signal on a low-traffic instance
    # while the backend was still hard-failing — silence is not evidence of
    # recovery, and a configuration failure never recovers on its own.
    # ``age_s`` discloses how stale the last observation is instead.
    age = now - float(ev.get("last_event_ts", 0.0))
    degraded = bool(ev.get("degraded"))
    return {
        "configured": ev.get("configured", "unknown"),
        "serving": ev.get("served_by", ev.get("configured", "unknown")),
        "serving_model": ev.get("served_model", ""),
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
    except Exception as exc:  # noqa: BLE001 — observability fallback
        log_swallowed_error(__name__, exc)
    return out


def annotate_block(workload: str, block: dict[str, Any]) -> dict[str, Any]:
    """Merge the live serving/degraded signal into an ``inference_routing`` block.

    ``block`` carries the *configured* intent (``provider``/``model``); this adds
    ``serving`` (what actually answered last) + ``degraded``. A workload with no
    recorded event reports ``serving: "unknown"`` — nothing has failed, but
    nothing has answered either, and claiming the configured provider is serving
    is the same intent-for-fact substitution this module exists to remove.
    Never raises.
    """
    try:
        snap = snapshot().get(workload)
        if snap is None:
            block.setdefault("serving", "unknown")
            block.setdefault("degraded", False)
            return block
        block["serving"] = snap["serving"]
        block["degraded"] = snap["degraded"]
        if snap["serving_model"]:
            block["serving_model"] = snap["serving_model"]
        if snap["degraded"] and snap["detail"]:
            block["degraded_detail"] = snap["detail"]
        block["fallback_count"] = snap["fallback_count"]
    except Exception:  # noqa: BLE001 — observability fallback
        block.setdefault("degraded", False)
    return block
