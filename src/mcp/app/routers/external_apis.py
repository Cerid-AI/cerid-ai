# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""External public-API management router (Phase API.1 + API.2).

Routes
------
GET  /external-apis
    List all 8 registered adapters with their enabled state and key status.

GET  /external-apis/{slug}/health
    Run the named adapter's health_check() and return the result.

POST /external-apis/{slug}/enabled
    Toggle the adapter's enabled state.  State is persisted in Redis.

All endpoints respect the existing auth middleware (``APIKeyMiddleware``) via
the standard router mount — no additional auth logic needed here.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.services.external_apis import registry as _registry
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.external_apis")

router = APIRouter(prefix="/external-apis", tags=["external-apis"])


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class AdapterEntry(BaseModel):
    """Wire-format for a single adapter catalogue entry."""

    slug: str
    display_name: str
    enabled: bool
    requires_key: bool
    key_configured: bool


class AdapterListResponse(BaseModel):
    """Response for ``GET /external-apis``."""

    adapters: list[AdapterEntry]
    total: int


class HealthResponse(BaseModel):
    """Response for ``GET /external-apis/{slug}/health``."""

    slug: str
    status: str  # "ok" | "error"
    detail: str | None = None


class EnabledRequest(BaseModel):
    """Body for ``POST /external-apis/{slug}/enabled``."""

    enabled: bool


class EnabledResponse(BaseModel):
    """Response for ``POST /external-apis/{slug}/enabled``."""

    slug: str
    enabled: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_redis_client():  # type: ignore[return]
    """Return the application Redis client, or None if unavailable."""
    try:
        from app.deps import get_redis
        return get_redis()
    except Exception:  # noqa: BLE001
        return None


def _require_adapter(slug: str):
    """Return the adapter for ``slug`` or raise 404."""
    adapter = _registry.get_adapter(slug)
    if adapter is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown adapter slug: {slug!r}",
        )
    return adapter


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=AdapterListResponse, summary="List all external API adapters")
def list_adapters() -> AdapterListResponse:
    """Return the full catalogue of registered adapters and their state."""
    redis = _get_redis_client()
    entries = _registry.list_adapters(redis_client=redis)
    return AdapterListResponse(
        adapters=[AdapterEntry(**e) for e in entries],
        total=len(entries),
    )


@router.get(
    "/{slug}/health",
    response_model=HealthResponse,
    summary="Check adapter health",
)
async def adapter_health(slug: str) -> HealthResponse:
    """Run the named adapter's health check and return the result.

    A non-2xx from the upstream counts as ``status="error"``; a reachable
    but 404-returning endpoint can still be ``status="ok"`` (see Wikipedia
    adapter docs).
    """
    adapter = _require_adapter(slug)
    try:
        ok = await adapter.health_check()
        return HealthResponse(slug=slug, status="ok" if ok else "error")
    except Exception as exc:  # noqa: BLE001
        log_swallowed_error(f"external_apis.{slug}.health_check", exc)
        return HealthResponse(slug=slug, status="error", detail=str(exc))


@router.post(
    "/{slug}/enabled",
    response_model=EnabledResponse,
    summary="Enable or disable an adapter",
)
def set_adapter_enabled(slug: str, body: EnabledRequest) -> EnabledResponse:
    """Toggle an adapter's enabled state.

    State is persisted in Redis (``cerid:external_apis:{slug}:enabled``).
    If Redis is unavailable the request returns 503.
    """
    _require_adapter(slug)
    redis = _get_redis_client()
    if redis is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis unavailable — cannot persist adapter state",
        )
    try:
        _registry.set_enabled(slug, body.enabled, redis_client=redis)
    except Exception as exc:
        log_swallowed_error(f"external_apis.{slug}.set_enabled", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to persist state: {exc}",
        ) from exc

    return EnabledResponse(slug=slug, enabled=body.enabled)
