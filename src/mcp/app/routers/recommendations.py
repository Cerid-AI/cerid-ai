# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Recommendations router — per-tenant dismissal of adaptive config nudges (C3.2).

Two endpoints:

* ``POST /settings/recommendations/{id}/dismiss`` — record a permanent
  per-tenant dismissal so the recommendation never re-appears for
  this tenant even if the corpus-size threshold is still satisfied.
* ``DELETE /settings/recommendations/{id}`` — clear the rec from the
  active hash *and* clear any dismissal record. Used by the
  "Enable now" path after the toggle successfully flips on, so the
  banner closes cleanly. (The next recommender tick would also clear
  it because the flag is now on, but the explicit DELETE makes the
  UI snappy.)

The 24h session-snooze case is handled client-side in
``recommendation-banner.tsx`` via ``sessionStorage`` — there's no
server state for that path so per-tab behavior is consistent.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.routers.recommendations")

router = APIRouter(prefix="/settings/recommendations", tags=["settings"])


_DISMISSED_SET_PREFIX = "cerid:recommendations:dismissed:"
_REDIS_HASH_KEY = "cerid:recommendations"


def _dismissed_key(tenant_id: str) -> str:
    return f"{_DISMISSED_SET_PREFIX}{tenant_id}"


def _resolve_tenant(request: Request) -> str:
    """Look up the request's tenant id with a safe default.

    Cerid runs single-tenant by default, so any installation that
    doesn't expose per-tenant headers gets the canonical ``default``
    tenant. Enterprise tier installations override via the
    ``X-Cerid-Tenant`` header.
    """
    header = request.headers.get("X-Cerid-Tenant")
    if header and header.strip():
        return header.strip()
    return "default"


@router.post("/{rec_id}/dismiss", status_code=204)
async def dismiss_recommendation(rec_id: str, request: Request) -> None:
    """Permanently dismiss a recommendation for this tenant.

    Idempotent — repeated calls with the same id leave Redis in the
    same state. The recommender job still writes the entry to the
    active hash; the ``/health`` filter consults this set and trims
    matches before returning.
    """
    if not rec_id or "/" in rec_id:
        raise HTTPException(status_code=400, detail="invalid recommendation id")

    from app.deps import get_redis
    try:
        redis_client = get_redis()
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error(
            "app.routers.recommendations.dismiss.get_redis", exc,
        )
        raise HTTPException(status_code=503, detail="redis unavailable") from None

    tenant = _resolve_tenant(request)
    try:
        redis_client.sadd(_dismissed_key(tenant), rec_id)
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("app.routers.recommendations.dismiss.sadd", exc)
        raise HTTPException(status_code=500, detail="dismiss failed") from None


@router.delete("/{rec_id}", status_code=204)
async def clear_recommendation(rec_id: str, request: Request) -> None:
    """Clear a recommendation from the active hash + drop any dismissal.

    Called after "Enable now" succeeds so the banner closes immediately
    rather than waiting for the next 6-hour recommender tick. Also
    drops the per-tenant dismissal entry — if the operator re-disables
    the flag later, we want the banner to be eligible to re-surface
    rather than silently suppressed.
    """
    if not rec_id or "/" in rec_id:
        raise HTTPException(status_code=400, detail="invalid recommendation id")

    from app.deps import get_redis
    try:
        redis_client = get_redis()
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error(
            "app.routers.recommendations.clear.get_redis", exc,
        )
        raise HTTPException(status_code=503, detail="redis unavailable") from None

    tenant = _resolve_tenant(request)
    try:
        with redis_client.pipeline() as pipe:
            pipe.hdel(_REDIS_HASH_KEY, rec_id)
            pipe.srem(_dismissed_key(tenant), rec_id)
            pipe.execute()
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error("app.routers.recommendations.clear.pipeline", exc)
        raise HTTPException(status_code=500, detail="clear failed") from None
